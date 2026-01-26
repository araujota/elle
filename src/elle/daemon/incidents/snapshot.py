"""System state snapshot collector.

Gathers point-in-time system metrics for incident analysis,
precondition matching, and comparison.
"""

import os
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from elle.daemon.incidents.models import Fingerprint, PackageState, SystemSnapshot

# =============================================================================
# Bedrock Packages
# =============================================================================

# Core system packages always tracked (~15 packages)
# These are fundamental to system operation and frequently relevant to incidents
BEDROCK_PACKAGES: tuple[str, ...] = (
    # Kernel and core runtime
    "linux-image-generic",
    "linux-headers-generic",
    "systemd",
    "libc6",
    # Python runtime (ELLE itself)
    "python3",
    "python3-pip",
    # Key infrastructure
    "openssl",
    "libssl3",
    "ca-certificates",
    "apt",
    "dpkg",
    # Networking
    "netplan.io",
    "systemd-resolved",
    # Security
    "polkitd",
    "sudo",
)


# =============================================================================
# Package Collection
# =============================================================================


def _get_package_version(name: str) -> str | None:
    """Get installed version of a package via dpkg-query.

    Args:
        name: Package name to query.

    Returns:
        Version string if installed, None otherwise.
    """
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", name],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _extract_relevant_packages(
    command: str | None,
    stderr: str | None,
    entity: str | None,
) -> set[str]:
    """Extract package names from incident context.

    Uses heuristics to identify packages that may be relevant
    to the incident being tracked.

    Args:
        command: The command that was executed (if any).
        stderr: Error output from the command (if any).
        entity: Entity field from the incident (if any).

    Returns:
        Set of package names extracted from context.
    """
    packages: set[str] = set()

    # From command: apt install X, dpkg -i X.deb, pip install X
    if command:
        # apt/apt-get install patterns
        apt_match = re.search(r"apt(?:-get)?\s+install\s+(\S+)", command)
        if apt_match:
            packages.add(apt_match.group(1))

        # dpkg -i pattern
        dpkg_match = re.search(r"dpkg\s+-i\s+(\S+?)(?:_|\.deb)", command)
        if dpkg_match:
            packages.add(dpkg_match.group(1))

        # pip install pattern
        pip_match = re.search(r"pip3?\s+install\s+(\S+)", command)
        if pip_match:
            packages.add(pip_match.group(1))

        # systemctl commands often reference service packages
        systemctl_match = re.search(r"systemctl\s+\S+\s+(\S+)", command)
        if systemctl_match:
            packages.add(systemctl_match.group(1))

    # From error: "Package 'X' not found", "Depends: X but..."
    if stderr:
        # Package mentions in error output
        pkg_mentions = re.findall(
            r"[Pp]ackage[:\s]+['\"]?(\w[\w\-\.]+)",
            stderr,
        )
        packages.update(pkg_mentions[:5])  # Limit extraction

        # Dependency mentions
        dep_mentions = re.findall(
            r"[Dd]epends:?\s+(\w[\w\-\.]+)",
            stderr,
        )
        packages.update(dep_mentions[:3])

    # From entity: service:nginx -> nginx
    if entity and ":" in entity:
        entity_name = entity.split(":", 1)[1]
        # Clean up entity name to get potential package
        pkg_name = entity_name.replace(".service", "").replace(".timer", "")
        packages.add(pkg_name)

    return packages


def collect_package_state(
    command: str | None = None,
    stderr: str | None = None,
    entity: str | None = None,
) -> tuple[PackageState, ...]:
    """Collect bedrock + relevant package versions.

    Always captures bedrock packages. Optionally extracts
    additional relevant packages from incident context.

    Args:
        command: Command that triggered the incident.
        stderr: Error output from the command.
        entity: Entity involved in the incident.

    Returns:
        Tuple of PackageState for installed packages.
    """
    states: list[PackageState] = []

    # Always collect bedrock packages
    for name in BEDROCK_PACKAGES:
        version = _get_package_version(name)
        if version:
            states.append(
                PackageState(
                    name=name,
                    version=version,
                    source="apt",
                    is_bedrock=True,
                )
            )

    # Collect relevant packages (deduplicated against bedrock)
    bedrock_names = set(BEDROCK_PACKAGES)
    relevant = _extract_relevant_packages(command, stderr, entity)

    for name in list(relevant)[:10]:  # Max 10 relevant packages
        if name not in bedrock_names:
            version = _get_package_version(name)
            if version:
                states.append(
                    PackageState(
                        name=name,
                        version=version,
                        source="apt",
                        is_bedrock=False,
                    )
                )

    return tuple(states)


# =============================================================================
# Snapshot Collection
# =============================================================================


def collect_snapshot(
    command: str | None = None,
    stderr: str | None = None,
    entity: str | None = None,
) -> SystemSnapshot:
    """Collect current system state.

    Gathers essential system metrics quickly and deterministically.
    All probes are designed to be fast and non-invasive.

    Args:
        command: Optional command that triggered snapshot collection.
        stderr: Optional error output for package extraction.
        entity: Optional entity name for package extraction.

    Returns:
        SystemSnapshot with current system state including package versions.
    """
    return SystemSnapshot(
        os=_get_os_info(),
        kernel=_get_kernel_version(),
        uptime_sec=_get_uptime(),
        hostname=_get_hostname(),
        cpu_load=_get_cpu_load(),
        mem_total_mb=_get_mem_total(),
        mem_free_mb=_get_mem_free(),
        mem_available_mb=_get_mem_available(),
        swap_total_mb=_get_swap_total(),
        swap_used_mb=_get_swap_used(),
        disks=tuple(_get_disk_info()),
        interfaces=tuple(_get_network_info()),
        services=tuple(_get_service_info()),
        docker_running=_get_docker_running(),
        docker_exited=_get_docker_exited(),
        docker_containers=tuple(_get_docker_containers()),
        temps=tuple(_get_temps()),
        smart=tuple(_get_smart_info()),
        packages=collect_package_state(command, stderr, entity),
        collected_at=datetime.utcnow(),
    )


def extract_fingerprint(
    snapshot: SystemSnapshot,
    oom_count_1h: int = 0,
    net_flaps_1h: int = 0,
    service_failures_1h: int = 0,
    auth_failures_1h: int = 0,
    entities: list[str] | None = None,
) -> Fingerprint:
    """Extract similarity features from a snapshot.

    Computes derived metrics for fast incident matching
    and precondition evaluation.

    Args:
        snapshot: System snapshot to analyze.
        oom_count_1h: OOM kills in the last hour.
        net_flaps_1h: Network state changes in the last hour.
        service_failures_1h: Service failures in the last hour.
        auth_failures_1h: Auth failures in the last hour.
        entities: Involved entity names.

    Returns:
        Fingerprint with derived features.
    """
    # Disk pressure: max usage across mounts
    disk_pressure = 0.0
    if snapshot.disks:
        disk_pressure = max(
            (d.get("used_pct", 0) / 100.0 for d in snapshot.disks),
            default=0.0,
        )

    # Memory pressure: 1 - (available / total)
    mem_pressure = 0.0
    if snapshot.mem_total_mb > 0:
        mem_pressure = 1.0 - (snapshot.mem_available_mb / snapshot.mem_total_mb)

    # Swap pressure
    swap_pressure = 0.0
    if snapshot.swap_total_mb > 0:
        swap_pressure = snapshot.swap_used_mb / snapshot.swap_total_mb

    # CPU pressure (1-min load average)
    cpu_pressure = snapshot.cpu_load[0] if snapshot.cpu_load else 0.0

    # SMART metrics
    smart_pct_used_max = 0
    smart_media_errors = 0
    for s in snapshot.smart:
        pct = s.get("pct_used", 0)
        if pct > smart_pct_used_max:
            smart_pct_used_max = pct
        smart_media_errors += s.get("media_errors", 0)

    # Temperature
    temp_max_c = 0
    for t in snapshot.temps:
        c = t.get("celsius", 0)
        if c > temp_max_c:
            temp_max_c = c

    return Fingerprint(
        disk_pressure=min(1.0, disk_pressure),
        mem_pressure=min(1.0, max(0.0, mem_pressure)),
        swap_pressure=min(1.0, swap_pressure),
        cpu_pressure=cpu_pressure,
        oom_count_1h=oom_count_1h,
        net_flaps_1h=net_flaps_1h,
        service_failures_1h=service_failures_1h,
        auth_failures_1h=auth_failures_1h,
        entities=tuple(entities or []),
        smart_pct_used_max=smart_pct_used_max,
        smart_media_errors=smart_media_errors,
        temp_max_c=temp_max_c,
        docker_exited_count=snapshot.docker_exited,
    )


def diff_snapshots(
    before: SystemSnapshot,
    after: SystemSnapshot,
) -> dict[str, Any]:
    """Compute differences between two snapshots.

    Useful for understanding what changed during incident handling.

    Args:
        before: Pre-action snapshot.
        after: Post-action snapshot.

    Returns:
        Dict describing changes.
    """
    diff: dict[str, Any] = {
        "uptime_delta_sec": after.uptime_sec - before.uptime_sec,
        "mem_free_delta_mb": after.mem_free_mb - before.mem_free_mb,
        "mem_available_delta_mb": after.mem_available_mb - before.mem_available_mb,
        "swap_used_delta_mb": after.swap_used_mb - before.swap_used_mb,
        "cpu_load_delta": (after.cpu_load[0] - before.cpu_load[0] if after.cpu_load and before.cpu_load else 0),
    }

    # Disk changes
    before_disks = {d["mount"]: d for d in before.disks}
    after_disks = {d["mount"]: d for d in after.disks}

    disk_changes = []
    for mount, after_d in after_disks.items():
        if mount in before_disks:
            before_d = before_disks[mount]
            pct_delta = after_d.get("used_pct", 0) - before_d.get("used_pct", 0)
            if abs(pct_delta) > 0.1:
                disk_changes.append(
                    {
                        "mount": mount,
                        "used_pct_delta": pct_delta,
                    }
                )
    diff["disk_changes"] = disk_changes

    # Interface changes
    before_ifaces = {i["name"]: i for i in before.interfaces}
    after_ifaces = {i["name"]: i for i in after.interfaces}

    iface_changes = []
    for name, after_i in after_ifaces.items():
        if name in before_ifaces:
            before_i = before_ifaces[name]
            if after_i.get("state") != before_i.get("state"):
                iface_changes.append(
                    {
                        "name": name,
                        "state_before": before_i.get("state"),
                        "state_after": after_i.get("state"),
                    }
                )
    diff["interface_changes"] = iface_changes

    # Service changes
    before_svc = {s["name"]: s for s in before.services}
    after_svc = {s["name"]: s for s in after.services}

    svc_changes = []
    for name, after_s in after_svc.items():
        if name in before_svc:
            before_s = before_svc[name]
            if after_s.get("active") != before_s.get("active"):
                svc_changes.append(
                    {
                        "name": name,
                        "active_before": before_s.get("active"),
                        "active_after": after_s.get("active"),
                    }
                )
    diff["service_changes"] = svc_changes

    # Docker changes
    diff["docker_running_delta"] = after.docker_running - before.docker_running
    diff["docker_exited_delta"] = after.docker_exited - before.docker_exited

    return diff


# =============================================================================
# Private probe functions
# =============================================================================


def _get_os_info() -> str:
    """Get OS name and version."""
    try:
        # Try reading /etc/os-release for Ubuntu/Debian
        os_release = Path("/etc/os-release")
        if os_release.exists():
            content = os_release.read_text()
            name = ""
            version = ""
            for line in content.split("\n"):
                if line.startswith("NAME="):
                    name = line.split("=", 1)[1].strip('"')
                elif line.startswith("VERSION_ID="):
                    version = line.split("=", 1)[1].strip('"')
            if name and version:
                return f"{name} {version}"
    except Exception:
        pass
    return platform.platform()


def _get_kernel_version() -> str:
    """Get kernel version."""
    try:
        return platform.release()
    except Exception:
        return "unknown"


def _get_uptime() -> int:
    """Get system uptime in seconds."""
    try:
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.read().split()[0])
            return int(uptime_seconds)
    except Exception:
        return 0


def _get_hostname() -> str:
    """Get system hostname."""
    try:
        return platform.node()
    except Exception:
        return "unknown"


def _get_cpu_load() -> tuple[float, float, float]:
    """Get CPU load averages (1, 5, 15 min)."""
    try:
        load = os.getloadavg()
        return (round(load[0], 2), round(load[1], 2), round(load[2], 2))
    except Exception:
        return (0.0, 0.0, 0.0)


def _get_mem_total() -> int:
    """Get total memory in MB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass
    return 0


def _get_mem_free() -> int:
    """Get free memory in MB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemFree:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass
    return 0


def _get_mem_available() -> int:
    """Get available memory in MB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass
    return 0


def _get_swap_total() -> int:
    """Get total swap in MB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("SwapTotal:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass
    return 0


def _get_swap_used() -> int:
    """Get used swap in MB."""
    try:
        total = 0
        free = 0
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("SwapTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    free = int(line.split()[1])
        return (total - free) // 1024
    except Exception:
        pass
    return 0


def _get_disk_info() -> list[dict[str, Any]]:
    """Get disk usage information."""
    disks = []
    try:
        result = subprocess.run(
            ["df", "-h", "--output=target,pcent,avail,source"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")[1:]  # Skip header
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    mount = parts[0]
                    # Skip pseudo-filesystems
                    if mount.startswith("/dev") or mount.startswith("/run") or mount.startswith("/sys"):
                        continue
                    if mount in ("/", "/home", "/var", "/tmp", "/boot"):
                        pct_str = parts[1].rstrip("%")
                        try:
                            used_pct = int(pct_str)
                        except ValueError:
                            continue
                        avail_str = parts[2]
                        avail_gb = _parse_size_to_gb(avail_str)
                        disks.append(
                            {
                                "mount": mount,
                                "used_pct": used_pct,
                                "avail_gb": avail_gb,
                                "device": parts[3] if len(parts) > 3 else "",
                            }
                        )
    except Exception:
        pass
    return disks


def _parse_size_to_gb(size_str: str) -> float:
    """Parse a size string like '120G' to GB."""
    try:
        if size_str.endswith("G"):
            return float(size_str[:-1])
        elif size_str.endswith("M"):
            return float(size_str[:-1]) / 1024
        elif size_str.endswith("T"):
            return float(size_str[:-1]) * 1024
        elif size_str.endswith("K"):
            return float(size_str[:-1]) / (1024 * 1024)
        else:
            return float(size_str)
    except ValueError:
        return 0.0


def _get_network_info() -> list[dict[str, Any]]:
    """Get network interface information."""
    interfaces = []
    try:
        result = subprocess.run(
            ["ip", "-j", "link", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import json

            links = json.loads(result.stdout)
            for link in links:
                name = link.get("ifname", "")
                # Skip loopback
                if name == "lo":
                    continue
                state = link.get("operstate", "UNKNOWN")
                # Get stats
                stats = link.get("stats64", {})
                rx_errors = stats.get("rx", {}).get("errors", 0)
                tx_errors = stats.get("tx", {}).get("errors", 0)

                interfaces.append(
                    {
                        "name": name,
                        "state": state,
                        "rx_err": rx_errors,
                        "tx_err": tx_errors,
                    }
                )
    except Exception:
        pass
    return interfaces


def _get_service_info() -> list[dict[str, Any]]:
    """Get key systemd service status."""
    services = []
    key_services = [
        "NetworkManager",
        "systemd-resolved",
        "docker",
        "sshd",
        "ssh",
        "ufw",
        "cron",
        "snapd",
    ]
    try:
        result = subprocess.run(
            ["systemctl", "is-active"] + key_services,
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Each line corresponds to a service
        lines = result.stdout.strip().split("\n")
        for i, svc_name in enumerate(key_services):
            if i < len(lines):
                status = lines[i].strip()
                services.append(
                    {
                        "name": svc_name,
                        "active": status == "active",
                        "failed": status == "failed",
                    }
                )
    except Exception:
        pass
    return services


def _get_docker_running() -> int:
    """Get number of running Docker containers."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-q"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return len([l for l in result.stdout.strip().split("\n") if l])
    except Exception:
        pass
    return 0


def _get_docker_exited() -> int:
    """Get number of exited Docker containers."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "-f", "status=exited"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return len([l for l in result.stdout.strip().split("\n") if l])
    except Exception:
        pass
    return 0


def _get_docker_containers() -> list[dict[str, Any]]:
    """Get Docker container details."""
    containers = []
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Image}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    containers.append(
                        {
                            "name": parts[0],
                            "state": parts[1],
                            "image": parts[2],
                        }
                    )
    except Exception:
        pass
    return containers


def _get_temps() -> list[dict[str, Any]]:
    """Get temperature sensor readings."""
    temps = []
    try:
        result = subprocess.run(
            ["sensors", "-j"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            for adapter, sensors in data.items():
                if isinstance(sensors, dict):
                    for sensor_name, readings in sensors.items():
                        if isinstance(readings, dict):
                            for key, value in readings.items():
                                if key.endswith("_input") and isinstance(value, (int, float)):
                                    temps.append(
                                        {
                                            "sensor": f"{adapter}/{sensor_name}",
                                            "celsius": int(value),
                                        }
                                    )
    except Exception:
        pass
    return temps


def _get_smart_info() -> list[dict[str, Any]]:
    """Get SMART disk health information."""
    smart = []
    # Find block devices
    try:
        result = subprocess.run(
            ["lsblk", "-d", "-n", "-o", "NAME,TYPE"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "disk":
                    device = f"/dev/{parts[0]}"
                    info = _get_smart_for_device(device)
                    if info:
                        smart.append(info)
    except Exception:
        pass
    return smart


def _get_smart_for_device(device: str) -> dict[str, Any] | None:
    """Get SMART info for a single device."""
    try:
        result = subprocess.run(
            ["smartctl", "-j", "-a", device],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode in (0, 4):  # 4 = some SMART data available
            import json

            data = json.loads(result.stdout)
            health = data.get("smart_status", {}).get("passed", True)
            pct_used = 0
            media_errors = 0

            # NVMe specific
            if "nvme_smart_health_information_log" in data:
                nvme = data["nvme_smart_health_information_log"]
                pct_used = nvme.get("percentage_used", 0)
                media_errors = nvme.get("media_errors", 0)

            return {
                "dev": device,
                "health": "PASSED" if health else "FAILED",
                "pct_used": pct_used,
                "media_errors": media_errors,
            }
    except Exception:
        pass
    return None
