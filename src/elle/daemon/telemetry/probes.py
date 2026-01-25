"""Periodic probes for system health.

Runs periodic checks via:
- /proc/meminfo (memory pressure)
- os.statvfs (disk pressure)
- /sys/class/net (network state)
- /sys/class/thermal (temperatures)
- smartctl (NVMe/SATA health)
"""

import asyncio
import json
import logging
import os
import subprocess
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elle.daemon.config import ThresholdConfig
from elle.daemon.telemetry.models import ProbeResult, TelemetryEvent
from elle.daemon.telemetry.queue import TelemetryQueue

logger = logging.getLogger(__name__)


class BaseProbe(ABC):
    """Base class for system probes."""

    name: str = "base"
    interval: int = 60  # seconds

    def __init__(self, thresholds: ThresholdConfig | None = None):
        """Initialize the probe.

        Args:
            thresholds: Threshold configuration.
        """
        self._thresholds = thresholds or ThresholdConfig()
        self._last_run: datetime | None = None
        self._run_count = 0
        self._error_count = 0

    @property
    def last_run(self) -> datetime | None:
        """Get last run time."""
        return self._last_run

    @abstractmethod
    async def run(self) -> ProbeResult:
        """Run the probe and return results.

        Returns:
            ProbeResult with probe data and any generated events.
        """
        pass

    def _create_event(
        self,
        message: str,
        severity: str = "warning",
        category: str = "other",
        entity: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> TelemetryEvent:
        """Create a TelemetryEvent from probe data.

        Args:
            message: Event message.
            severity: Event severity.
            category: Event category.
            entity: Affected entity.
            data: Additional data for raw field.

        Returns:
            TelemetryEvent instance.
        """
        return TelemetryEvent(
            ts=datetime.now(UTC),
            source="probe",
            severity=severity,  # type: ignore
            category=category,
            message=message,
            entity=entity,
            fingerprint=TelemetryEvent.compute_fingerprint(category, entity, message),
            raw=data or {},
        )


class MemoryProbe(BaseProbe):
    """Probe memory usage via /proc/meminfo.

    Generates warning when memory pressure exceeds threshold.
    """

    name = "memory"
    interval = 30  # 30 seconds

    async def run(self) -> ProbeResult:
        """Check memory pressure."""
        self._last_run = datetime.now(UTC)
        self._run_count += 1

        try:
            data = await self._read_meminfo()
            events = []

            # Calculate pressure
            total = data.get("memtotal_kb", 0)
            available = data.get("memavailable_kb", 0)

            if total > 0:
                pressure = 1.0 - (available / total)
                data["mem_pressure"] = round(pressure, 3)

                if pressure > self._thresholds.memory_warning_pct:
                    events.append(self._create_event(
                        message=f"High memory pressure: {pressure:.1%} used",
                        severity="warning",
                        category="oom",
                        data=data,
                    ))

            # Check swap
            swap_total = data.get("swaptotal_kb", 0)
            swap_free = data.get("swapfree_kb", 0)
            if swap_total > 0:
                swap_used = swap_total - swap_free
                swap_pressure = swap_used / swap_total
                data["swap_pressure"] = round(swap_pressure, 3)

                if swap_pressure > 0.8:
                    events.append(self._create_event(
                        message=f"High swap usage: {swap_pressure:.1%} used",
                        severity="warning",
                        category="oom",
                        data=data,
                    ))

            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,
                data=data,
                events=tuple(events),
            )

        except Exception as e:
            self._error_count += 1
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=False,
                data={},
                error=str(e),
            )

    async def _read_meminfo(self) -> dict[str, Any]:
        """Read /proc/meminfo asynchronously."""
        data = {}
        try:
            content = await asyncio.to_thread(Path("/proc/meminfo").read_text)
            for line in content.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower().replace(" ", "_")
                    # Parse value (remove "kB" suffix)
                    value = value.strip().split()[0]
                    try:
                        data[f"{key}_kb"] = int(value)
                    except ValueError:
                        pass
        except Exception:
            pass
        return data


class DiskProbe(BaseProbe):
    """Probe disk usage via os.statvfs.

    Generates warning when any mount exceeds threshold.
    """

    name = "disk"
    interval = 300  # 5 minutes

    # Mounts to check (skip pseudo-filesystems)
    MOUNT_PREFIXES = ("/", "/home", "/var", "/tmp", "/boot", "/opt")
    SKIP_TYPES = ("squashfs", "tmpfs", "devtmpfs", "overlay", "cgroup", "proc", "sysfs")

    async def run(self) -> ProbeResult:
        """Check disk usage."""
        self._last_run = datetime.now(UTC)
        self._run_count += 1

        try:
            mounts = await self._get_mounts()
            events = []
            data = {"mounts": mounts}

            for mount in mounts:
                used_pct = mount.get("used_pct", 0)
                if used_pct > self._thresholds.disk_warning_pct * 100:
                    events.append(self._create_event(
                        message=f"Disk space low on {mount['mount']}: {used_pct:.1f}% used",
                        severity="warning",
                        category="disk",
                        entity=f"mount:{mount['mount']}",
                        data=mount,
                    ))

            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,
                data=data,
                events=tuple(events),
            )

        except Exception as e:
            self._error_count += 1
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=False,
                data={},
                error=str(e),
            )

    async def _get_mounts(self) -> list[dict[str, Any]]:
        """Get mount point usage statistics."""
        mounts = []

        def _check_mounts() -> list[dict[str, Any]]:
            results = []
            try:
                with open("/proc/mounts") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 4:
                            device, mount, fstype = parts[0], parts[1], parts[2]

                            # Skip pseudo-filesystems
                            if fstype in self.SKIP_TYPES:
                                continue
                            if not mount.startswith(self.MOUNT_PREFIXES):
                                continue

                            try:
                                stat = os.statvfs(mount)
                                total = stat.f_blocks * stat.f_frsize
                                free = stat.f_bavail * stat.f_frsize
                                used = total - free

                                if total > 0:
                                    results.append({
                                        "mount": mount,
                                        "device": device,
                                        "fstype": fstype,
                                        "total_gb": round(total / (1024 ** 3), 2),
                                        "used_gb": round(used / (1024 ** 3), 2),
                                        "free_gb": round(free / (1024 ** 3), 2),
                                        "used_pct": round(100 * used / total, 1),
                                    })
                            except OSError:
                                continue
            except Exception:
                pass
            return results

        mounts = await asyncio.to_thread(_check_mounts)
        return mounts


class NetworkProbe(BaseProbe):
    """Probe network interface state.

    Monitors link state and error counters.
    """

    name = "network"
    interval = 60  # 60 seconds

    def __init__(self, thresholds: ThresholdConfig | None = None):
        super().__init__(thresholds)
        self._prev_errors: dict[str, dict[str, int]] = {}

    async def run(self) -> ProbeResult:
        """Check network interface state."""
        self._last_run = datetime.now(UTC)
        self._run_count += 1

        try:
            interfaces = await self._get_interfaces()
            events = []
            data = {"interfaces": interfaces}

            for iface in interfaces:
                name = iface["name"]
                state = iface.get("state", "unknown")

                # Check for link down
                if state == "down":
                    events.append(self._create_event(
                        message=f"Network interface {name} is down",
                        severity="warning",
                        category="net",
                        entity=f"interface:{name}",
                        data=iface,
                    ))

                # Check for new errors
                rx_err = iface.get("rx_errors", 0)
                tx_err = iface.get("tx_errors", 0)

                prev = self._prev_errors.get(name, {})
                prev_rx = prev.get("rx_errors", 0)
                prev_tx = prev.get("tx_errors", 0)

                new_errors = (rx_err - prev_rx) + (tx_err - prev_tx)
                if new_errors > self._thresholds.network_error_threshold:
                    events.append(self._create_event(
                        message=f"Network errors on {name}: {new_errors} new errors",
                        severity="warning",
                        category="net",
                        entity=f"interface:{name}",
                        data=iface,
                    ))

                # Update previous errors
                self._prev_errors[name] = {"rx_errors": rx_err, "tx_errors": tx_err}

            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,
                data=data,
                events=tuple(events),
            )

        except Exception as e:
            self._error_count += 1
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=False,
                data={},
                error=str(e),
            )

    async def _get_interfaces(self) -> list[dict[str, Any]]:
        """Get network interface information from sysfs."""
        interfaces = []

        def _read_interfaces() -> list[dict[str, Any]]:
            results = []
            net_path = Path("/sys/class/net")

            if not net_path.exists():
                return results

            for iface_path in net_path.iterdir():
                name = iface_path.name
                if name == "lo":  # Skip loopback
                    continue

                try:
                    iface = {"name": name}

                    # Read operstate
                    operstate = (iface_path / "operstate").read_text().strip()
                    iface["state"] = operstate

                    # Read error counters
                    stats_path = iface_path / "statistics"
                    if stats_path.exists():
                        try:
                            iface["rx_errors"] = int(
                                (stats_path / "rx_errors").read_text().strip()
                            )
                            iface["tx_errors"] = int(
                                (stats_path / "tx_errors").read_text().strip()
                            )
                            iface["rx_bytes"] = int(
                                (stats_path / "rx_bytes").read_text().strip()
                            )
                            iface["tx_bytes"] = int(
                                (stats_path / "tx_bytes").read_text().strip()
                            )
                        except (FileNotFoundError, ValueError):
                            pass

                    results.append(iface)
                except Exception:
                    continue

            return results

        interfaces = await asyncio.to_thread(_read_interfaces)
        return interfaces


class ThermalProbe(BaseProbe):
    """Probe thermal zones for temperature.

    Monitors CPU and other thermal zones.
    """

    name = "thermal"
    interval = 60  # 60 seconds

    async def run(self) -> ProbeResult:
        """Check thermal zone temperatures."""
        self._last_run = datetime.now(UTC)
        self._run_count += 1

        try:
            temps = await self._get_temps()
            events = []
            data = {"temps": temps}

            for temp in temps:
                celsius = temp.get("celsius", 0)
                zone = temp.get("zone", "unknown")

                if celsius > self._thresholds.thermal_warning_c:
                    events.append(self._create_event(
                        message=f"High temperature on {zone}: {celsius}C",
                        severity="warning",
                        category="thermal",
                        entity=f"thermal:{zone}",
                        data=temp,
                    ))

            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,
                data=data,
                events=tuple(events),
            )

        except Exception as e:
            self._error_count += 1
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=False,
                data={},
                error=str(e),
            )

    async def _get_temps(self) -> list[dict[str, Any]]:
        """Read thermal zone temperatures from sysfs."""
        temps = []

        def _read_temps() -> list[dict[str, Any]]:
            results = []
            thermal_path = Path("/sys/class/thermal")

            if not thermal_path.exists():
                return results

            for zone_path in thermal_path.glob("thermal_zone*"):
                try:
                    zone_name = zone_path.name

                    # Read zone type
                    zone_type = "unknown"
                    type_file = zone_path / "type"
                    if type_file.exists():
                        zone_type = type_file.read_text().strip()

                    # Read temperature (in millidegrees)
                    temp_file = zone_path / "temp"
                    if temp_file.exists():
                        temp_mdeg = int(temp_file.read_text().strip())
                        temp_celsius = temp_mdeg // 1000

                        results.append({
                            "zone": zone_name,
                            "type": zone_type,
                            "celsius": temp_celsius,
                        })
                except Exception:
                    continue

            return results

        temps = await asyncio.to_thread(_read_temps)
        return temps


class SmartProbe(BaseProbe):
    """Probe SMART disk health via smartctl.

    Runs smartctl on all block devices and checks health status.
    Gracefully degrades when smartctl is not installed.
    """

    name = "smart"
    interval = 3600  # 1 hour

    def __init__(self, thresholds: ThresholdConfig | None = None):
        """Initialize the SMART probe.

        Args:
            thresholds: Threshold configuration.
        """
        super().__init__(thresholds)
        self._smartctl_available: bool | None = None  # Lazy check

    def _check_smartctl(self) -> bool:
        """Check if smartctl is available on the system.

        Returns:
            True if smartctl is installed and accessible.
        """
        try:
            result = subprocess.run(
                ["which", "smartctl"],
                capture_output=True,
                timeout=2,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def run(self) -> ProbeResult:
        """Check SMART health for all disks."""
        self._last_run = datetime.now(UTC)
        self._run_count += 1

        # Lazy check for smartctl availability
        if self._smartctl_available is None:
            self._smartctl_available = self._check_smartctl()

        # Graceful degradation when smartctl not installed
        if not self._smartctl_available:
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,  # Not a failure, just unavailable
                data={"error": "smartctl not installed", "available": False},
                events=(),
            )

        try:
            disks = await self._get_smart_info()
            events = []
            data = {"disks": disks, "available": True}

            for disk in disks:
                health = disk.get("health", "UNKNOWN")
                device = disk.get("device", "unknown")

                if health not in ("PASSED", "OK"):
                    events.append(self._create_event(
                        message=f"SMART health check failed for {device}: {health}",
                        severity="critical",
                        category="smart",
                        entity=f"disk:{device}",
                        data=disk,
                    ))

                # Check for high percentage used (NVMe)
                pct_used = disk.get("pct_used", 0)
                if pct_used > 90:
                    events.append(self._create_event(
                        message=f"High wear on {device}: {pct_used}% used",
                        severity="warning",
                        category="smart",
                        entity=f"disk:{device}",
                        data=disk,
                    ))

                # Check for media errors
                media_errors = disk.get("media_errors", 0)
                if media_errors > 0:
                    events.append(self._create_event(
                        message=f"Media errors detected on {device}: {media_errors} errors",
                        severity="warning",
                        category="smart",
                        entity=f"disk:{device}",
                        data=disk,
                    ))

            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=True,
                data=data,
                events=tuple(events),
            )

        except Exception as e:
            self._error_count += 1
            return ProbeResult(
                probe_name=self.name,
                ts=self._last_run,
                success=False,
                data={},
                error=str(e),
            )

    async def _get_smart_info(self) -> list[dict[str, Any]]:
        """Get SMART info for all block devices."""
        disks = []

        # Get list of block devices
        devices = await self._list_block_devices()

        for device in devices:
            info = await self._get_smart_for_device(device)
            if info:
                disks.append(info)

        return disks

    async def _list_block_devices(self) -> list[str]:
        """List block devices using lsblk."""
        devices = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "lsblk", "-d", "-n", "-o", "NAME,TYPE",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()

            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "disk":
                    devices.append(f"/dev/{parts[0]}")

        except Exception:
            pass
        return devices

    async def _get_smart_for_device(self, device: str) -> dict[str, Any] | None:
        """Get SMART info for a single device."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "smartctl", "-j", "-a", device,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()

            # smartctl returns non-zero even with valid data
            if stdout:
                data = json.loads(stdout.decode())

                result: dict[str, Any] = {"device": device}

                # Health status
                smart_status = data.get("smart_status", {})
                result["health"] = "PASSED" if smart_status.get("passed", True) else "FAILED"

                # NVMe specific
                nvme_log = data.get("nvme_smart_health_information_log", {})
                if nvme_log:
                    result["pct_used"] = nvme_log.get("percentage_used", 0)
                    result["media_errors"] = nvme_log.get("media_errors", 0)
                    result["power_on_hours"] = nvme_log.get("power_on_hours", 0)

                # Model and serial
                result["model"] = data.get("model_name", "unknown")
                result["serial"] = data.get("serial_number", "unknown")

                return result

        except Exception:
            pass
        return None


class ProbeRunner:
    """Runs multiple probes on their configured intervals."""

    def __init__(
        self,
        queue: TelemetryQueue[dict[str, Any]],
        shutdown: asyncio.Event,
        thresholds: ThresholdConfig | None = None,
    ):
        """Initialize the probe runner.

        Args:
            queue: Queue to put raw events.
            shutdown: Event to signal shutdown.
            thresholds: Threshold configuration.
        """
        self._queue = queue
        self._shutdown = shutdown
        self._thresholds = thresholds or ThresholdConfig()
        self._probes: list[BaseProbe] = []
        self._running = False

    def add_probe(self, probe: BaseProbe) -> None:
        """Add a probe to run."""
        self._probes.append(probe)

    @property
    def running(self) -> bool:
        """Check if runner is active."""
        return self._running

    async def start(self) -> None:
        """Start running all probes."""
        logger.info(f"Starting probe runner with {len(self._probes)} probes")
        self._running = True

        # Create tasks for each probe
        tasks = [
            asyncio.create_task(self._run_probe_loop(probe))
            for probe in self._probes
        ]

        # Wait for shutdown
        await self._shutdown.wait()

        # Cancel all probe tasks
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        self._running = False
        logger.info("Probe runner stopped")

    async def _run_probe_loop(self, probe: BaseProbe) -> None:
        """Run a single probe on its interval."""
        logger.debug(f"Starting probe: {probe.name} (interval: {probe.interval}s)")

        while not self._shutdown.is_set():
            try:
                # Run the probe
                result = await probe.run()

                # Queue any events
                for event in result.events:
                    raw = event.raw.copy()
                    raw["_SOURCE"] = "probe"
                    raw["_PROBE"] = probe.name
                    raw["MESSAGE"] = event.message
                    await self._queue.put(raw)

                # Wait for next interval
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=probe.interval,
                    )
                    break
                except asyncio.TimeoutError:
                    continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Probe {probe.name} error: {e}")
                # Backoff on error
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=min(probe.interval, 60),
                    )
                    break
                except asyncio.TimeoutError:
                    continue


def create_default_probes(
    thresholds: ThresholdConfig | None = None,
) -> list[BaseProbe]:
    """Create the default set of probes.

    Args:
        thresholds: Threshold configuration.

    Returns:
        List of probe instances.
    """
    return [
        MemoryProbe(thresholds),
        DiskProbe(thresholds),
        NetworkProbe(thresholds),
        ThermalProbe(thresholds),
        SmartProbe(thresholds),
    ]


async def run_probes(
    queue: TelemetryQueue[dict[str, Any]],
    shutdown: asyncio.Event,
    thresholds: ThresholdConfig | None = None,
) -> None:
    """Run all default probes.

    Convenience function wrapping ProbeRunner.

    Args:
        queue: Queue to put raw events.
        shutdown: Event to signal shutdown.
        thresholds: Threshold configuration.
    """
    runner = ProbeRunner(queue, shutdown, thresholds)
    for probe in create_default_probes(thresholds):
        runner.add_probe(probe)
    await runner.start()
