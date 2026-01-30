"""System state snapshot collector.

Gathers point-in-time system metrics for incident analysis,
precondition matching, and comparison.
"""

from __future__ import annotations

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

# Core system packages always tracked (~30 packages)
# These are fundamental to system operation and frequently relevant to incidents
BEDROCK_PACKAGES: tuple[str, ...] = (
    # Kernel and core runtime
    "linux-image-generic",
    "linux-headers-generic",
    "systemd",
    "libc6",
    "glibc",
    # Python runtime (ELLE itself)
    "python3",
    "python3-pip",
    "python3-venv",
    # Key infrastructure
    "openssl",
    "libssl3",
    "ca-certificates",
    "apt",
    "dpkg",
    "gnupg",
    # Networking
    "netplan.io",
    "systemd-resolved",
    "iproute2",
    "iptables",
    "nftables",
    # Security
    "polkitd",
    "sudo",
    "apparmor",
    # Container runtime
    "docker.io",
    "containerd",
    "runc",
    # Observability
    "lm-sensors",
    "smartmontools",
)

# Domain-specific packages to check based on incident domain
DOMAIN_PACKAGES: dict[str, tuple[str, ...]] = {
    "docker": (
        "docker.io",
        "docker-ce",
        "containerd",
        "runc",
        "docker-compose",
        "docker-buildx",
    ),
    "net": (
        "iproute2",
        "iptables",
        "nftables",
        "ufw",
        "wireguard",
        "openvpn",
        "bind9",
        "dnsmasq",
        "nginx",
        "apache2",
        "haproxy",
    ),
    "auth": (
        "libpam-modules",
        "openssh-server",
        "sssd",
        "krb5-user",
        "samba",
        "sudo",
    ),
    "disk": (
        "lvm2",
        "mdadm",
        "cryptsetup",
        "parted",
        "e2fsprogs",
        "xfsprogs",
        "btrfs-progs",
        "nfs-common",
        "cifs-utils",
    ),
    "oom": (
        "earlyoom",
        "systemd-oomd",
    ),
    "service": (
        "systemd",
        "dbus",
    ),
}


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


def _get_pip_packages(relevant_only: bool = True) -> list[PackageState]:
    """Get installed pip packages.

    Args:
        relevant_only: If True, only return packages that seem relevant
                      (skip standard library wrappers).

    Returns:
        List of PackageState for pip packages.
    """
    states: list[PackageState] = []
    try:
        result = subprocess.run(
            ["pip3", "list", "--format=freeze"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Key packages to always include
            key_packages = {
                "requests",
                "urllib3",
                "aiohttp",
                "httpx",  # HTTP
                "sqlalchemy",
                "psycopg2",
                "pymysql",
                "redis",  # Databases
                "flask",
                "django",
                "fastapi",
                "uvicorn",  # Web frameworks
                "celery",
                "pika",
                "kafka-python",  # Messaging
                "boto3",
                "google-cloud-core",
                "azure-core",  # Cloud
                "cryptography",
                "pyjwt",
                "paramiko",  # Security
                "numpy",
                "pandas",
                "scipy",  # Data science
                "pydantic",
                "attrs",
                "dataclasses",  # Data modeling
            }
            for line in result.stdout.strip().split("\n"):
                if "==" in line:
                    name, version = line.split("==", 1)
                    name = name.strip().lower()
                    # Include if it's a key package or we want all
                    if not relevant_only or name in key_packages:
                        states.append(
                            PackageState(
                                name=f"pip:{name}",
                                version=version.strip(),
                                source="pip",
                                is_bedrock=False,
                            )
                        )
    except Exception:
        pass
    return states[:30]  # Limit to 30 pip packages


def _get_kernel_modules() -> list[dict[str, str]]:
    """Get loaded kernel modules.

    Returns:
        List of dicts with name and size for loaded modules.
    """
    modules: list[dict[str, str]] = []
    try:
        result = subprocess.run(
            ["lsmod"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")[1:]  # Skip header
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    modules.append(
                        {
                            "name": parts[0],
                            "size": parts[1],
                        }
                    )
    except Exception:
        pass
    return modules


def _get_docker_image_versions() -> list[dict[str, str]]:
    """Get Docker image versions for running containers.

    Returns:
        List of dicts with image name, tag, and digest.
    """
    images: list[dict[str, str]] = []
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Image}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            seen: set[str] = set()
            for image in result.stdout.strip().split("\n"):
                if image and image not in seen:
                    seen.add(image)
                    # Get digest for the image
                    digest_result = subprocess.run(
                        ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", image],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    digest = ""
                    if digest_result.returncode == 0:
                        digest = digest_result.stdout.strip()

                    # Parse image:tag
                    if ":" in image:
                        name, tag = image.rsplit(":", 1)
                    else:
                        name, tag = image, "latest"

                    images.append(
                        {
                            "image": name,
                            "tag": tag,
                            "digest": digest[:32] if digest else "",
                        }
                    )
    except Exception:
        pass
    return images[:20]  # Limit to 20 images


def collect_package_state(
    command: str | None = None,
    stderr: str | None = None,
    entity: str | None = None,
    domain: str | None = None,
    include_pip: bool = False,
) -> tuple[PackageState, ...]:
    """Collect bedrock + relevant package versions.

    Always captures bedrock packages. Optionally extracts
    additional relevant packages from incident context and domain.

    Args:
        command: Command that triggered the incident.
        stderr: Error output from the command.
        entity: Entity involved in the incident.
        domain: Incident domain (docker, net, auth, disk, etc.).
        include_pip: Whether to include pip packages.

    Returns:
        Tuple of PackageState for installed packages.
    """
    states: list[PackageState] = []
    seen_names: set[str] = set()

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
            seen_names.add(name)

    # Collect domain-specific packages
    if domain and domain in DOMAIN_PACKAGES:
        for name in DOMAIN_PACKAGES[domain]:
            if name not in seen_names:
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
                    seen_names.add(name)

    # Collect context-relevant packages
    relevant = _extract_relevant_packages(command, stderr, entity)
    for name in list(relevant)[:10]:  # Max 10 relevant packages
        if name not in seen_names:
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
                seen_names.add(name)

    # Optionally include pip packages
    if include_pip:
        pip_packages = _get_pip_packages(relevant_only=True)
        states.extend(pip_packages)

    return tuple(states)


# =============================================================================
# Snapshot Collection
# =============================================================================


def _get_recent_apt_history() -> list[dict[str, Any]]:
    """Get recent apt operations from /var/log/apt/history.log.

    Returns:
        List of recent apt operations in the last 24 hours.
    """
    history: list[dict[str, Any]] = []
    try:
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=24)
        history_file = Path("/var/log/apt/history.log")

        if history_file.exists():
            content = history_file.read_text()
            # Parse apt history format
            current_entry: dict[str, Any] = {}
            for line in content.split("\n"):
                if line.startswith("Start-Date:"):
                    date_str = line.split(":", 1)[1].strip()
                    try:
                        # Format: 2024-01-26  15:30:00
                        ts = datetime.strptime(date_str, "%Y-%m-%d  %H:%M:%S")
                        if ts < cutoff:
                            continue
                        current_entry = {"timestamp": ts.isoformat()}
                    except ValueError:
                        continue
                elif line.startswith("Commandline:") and current_entry:
                    current_entry["command"] = line.split(":", 1)[1].strip()
                elif line.startswith("Install:") and current_entry:
                    current_entry["action"] = "install"
                    current_entry["packages"] = line.split(":", 1)[1].strip()[:200]
                    history.append(current_entry)
                    current_entry = {}
                elif line.startswith("Upgrade:") and current_entry:
                    current_entry["action"] = "upgrade"
                    current_entry["packages"] = line.split(":", 1)[1].strip()[:200]
                    history.append(current_entry)
                    current_entry = {}
                elif line.startswith("Remove:") and current_entry:
                    current_entry["action"] = "remove"
                    current_entry["packages"] = line.split(":", 1)[1].strip()[:200]
                    history.append(current_entry)
                    current_entry = {}
    except Exception:
        pass
    return history[-20:]  # Last 20 operations


def collect_snapshot(
    command: str | None = None,
    stderr: str | None = None,
    entity: str | None = None,
    domain: str | None = None,
    include_pip: bool = False,
    include_kernel_modules: bool = True,
    include_docker_images: bool = True,
    include_apt_history: bool = True,
) -> SystemSnapshot:
    """Collect current system state.

    Gathers essential system metrics quickly and deterministically.
    All probes are designed to be fast and non-invasive.

    This creates a "maximum-surface snapshot" capturing:
    - OS/kernel versions
    - Resource utilization (CPU, memory, disk, swap)
    - Network interface states
    - Service states
    - Docker container and image states
    - Temperature and SMART data
    - Package versions (bedrock + domain-specific + context-relevant)
    - Kernel modules (for driver/hardware issues)
    - Recent apt history (for upgrade-related issues)

    Args:
        command: Optional command that triggered snapshot collection.
        stderr: Optional error output for package extraction.
        entity: Optional entity name for package extraction.
        domain: Optional incident domain for domain-specific packages.
        include_pip: Whether to include Python pip packages.
        include_kernel_modules: Whether to include loaded kernel modules.
        include_docker_images: Whether to include Docker image versions.
        include_apt_history: Whether to include recent apt operations.

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
        packages=collect_package_state(command, stderr, entity, domain, include_pip),
        kernel_modules=tuple(_get_kernel_modules()) if include_kernel_modules else (),
        docker_images=tuple(_get_docker_image_versions()) if include_docker_images else (),
        recent_apt_history=tuple(_get_recent_apt_history()) if include_apt_history else (),
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

    # GPU pressure metrics (if available)
    gpu_mem_pressure = 0.0
    gpu_util_pressure = 0.0
    gpu_thermal_pressure = 0.0

    if snapshot.gpu_available:
        # Normalize percentages to 0.0-1.0 range
        if snapshot.gpu_max_memory_pct > 0:
            gpu_mem_pressure = min(1.0, snapshot.gpu_max_memory_pct / 100.0)
        if snapshot.gpu_max_util_pct > 0:
            gpu_util_pressure = min(1.0, snapshot.gpu_max_util_pct / 100.0)
        if snapshot.gpu_max_temp_c > 0:
            # Normalize: 0°C=0.0, 100°C=1.0
            gpu_thermal_pressure = min(1.0, snapshot.gpu_max_temp_c / 100.0)

    # Inode pressure
    inode_info = _get_inode_usage()
    inode_pressure = 0.0
    if inode_info:
        inode_pressure = max(d.get("used_pct", 0) / 100.0 for d in inode_info)

    # I/O latency pressure (normalized: 100ms+ = 1.0)
    io_latency_pressure = 0.0
    io_stats = _get_io_stats()
    if io_stats:
        for stat in io_stats:
            rd_ios = stat.get("rd_ios", 0)
            wr_ios = stat.get("wr_ios", 0)
            rd_ticks = stat.get("rd_ticks", 0)
            wr_ticks = stat.get("wr_ticks", 0)
            if rd_ios > 0:
                rd_lat = rd_ticks / rd_ios
                io_latency_pressure = max(io_latency_pressure, min(rd_lat / 100.0, 1.0))
            if wr_ios > 0:
                wr_lat = wr_ticks / wr_ios
                io_latency_pressure = max(io_latency_pressure, min(wr_lat / 100.0, 1.0))

    # Conntrack pressure
    conntrack = _get_conntrack_stats()
    conntrack_pressure = min(conntrack.get("utilization", 0) / 100.0, 1.0)

    # TCP retransmit rate
    tcp_retransmit_rate = min(_get_tcp_retransmit_rate(), 1.0)

    # Zombie count
    zombie_count = _get_zombie_count()

    # Pending reboot
    pending_reboot = 1 if _get_pending_reboot() else 0

    # Certificate expiry (minimum days)
    cert_info = _get_cert_expiry()
    cert_expiry_days_min = 365
    if cert_info:
        cert_expiry_days_min = min(d.get("days_until_expiry", 365) for d in cert_info)
        cert_expiry_days_min = max(0, cert_expiry_days_min)

    # DNS p95 latency
    dns_latency = _get_dns_latency()
    dns_p95_ms = dns_latency.get("p95", 0.0)

    # Cgroup memory pressure
    cgroup_info = _get_cgroup_pressure()
    cgroup_mem_pressure = 0.0
    if cgroup_info:
        cgroup_mem_pressure = min(max(d.get("mem_pct", 0) / 100.0 for d in cgroup_info), 1.0)

    # PSI pressure
    psi_info = _get_psi_pressure()
    psi_cpu_avg10 = min(psi_info.get("cpu_avg10", 0.0) / 100.0, 1.0)
    psi_memory_avg10 = min(psi_info.get("mem_avg10", 0.0) / 100.0, 1.0)

    # Security events
    security_events_1h = _get_security_events()

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
        gpu_mem_pressure=gpu_mem_pressure,
        gpu_util_pressure=gpu_util_pressure,
        gpu_thermal_pressure=gpu_thermal_pressure,
        gpu_ecc_errors_1h=snapshot.gpu_ecc_errors if hasattr(snapshot, 'gpu_ecc_errors') else 0,
        # New monitoring sprint fields
        inode_pressure=min(1.0, inode_pressure),
        io_latency_pressure=min(1.0, io_latency_pressure),
        conntrack_pressure=min(1.0, conntrack_pressure),
        tcp_retransmit_rate=tcp_retransmit_rate,
        zombie_count=zombie_count,
        pending_reboot=pending_reboot,
        cert_expiry_days_min=cert_expiry_days_min,
        dns_p95_ms=dns_p95_ms,
        cgroup_mem_pressure=min(1.0, cgroup_mem_pressure),
        psi_cpu_avg10=psi_cpu_avg10,
        psi_memory_avg10=psi_memory_avg10,
        security_events_1h=security_events_1h,
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


def _get_inode_usage() -> list[dict[str, Any]]:
    """Get inode usage for tracked mount points."""
    inodes = []
    try:
        for mount in ["/", "/home", "/var", "/tmp", "/boot"]:
            try:
                stat = os.statvfs(mount)
                total = stat.f_files
                free = stat.f_ffree
                if total > 0:
                    used_pct = ((total - free) / total) * 100
                    inodes.append({
                        "mount": mount,
                        "total": total,
                        "free": free,
                        "used_pct": round(used_pct, 2),
                    })
            except (OSError, FileNotFoundError):
                pass
    except Exception:
        pass
    return inodes


def _get_io_stats() -> list[dict[str, Any]]:
    """Get I/O statistics from /proc/diskstats."""
    stats = []
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 14:
                    dev = parts[2]
                    # Skip partitions (ending in digit) and device-mapper
                    if dev[-1].isdigit() or dev.startswith("dm-") or dev.startswith("loop"):
                        continue
                    stats.append({
                        "dev": dev,
                        "rd_ios": int(parts[3]),
                        "rd_sectors": int(parts[5]),
                        "rd_ticks": int(parts[6]),
                        "wr_ios": int(parts[7]),
                        "wr_sectors": int(parts[9]),
                        "wr_ticks": int(parts[10]),
                        "ios_in_progress": int(parts[11]),
                        "io_ticks": int(parts[12]),
                    })
    except (OSError, FileNotFoundError):
        pass
    return stats


def _get_conntrack_stats() -> dict[str, Any]:
    """Get conntrack table statistics."""
    result: dict[str, Any] = {"count": 0, "max": 0, "utilization": 0.0}
    try:
        count_path = Path("/proc/sys/net/netfilter/nf_conntrack_count")
        max_path = Path("/proc/sys/net/netfilter/nf_conntrack_max")
        if count_path.exists() and max_path.exists():
            count = int(count_path.read_text().strip())
            max_val = int(max_path.read_text().strip())
            result = {
                "count": count,
                "max": max_val,
                "utilization": round((count / max_val) * 100, 2) if max_val > 0 else 0.0,
            }
    except (OSError, ValueError):
        pass
    return result


def _get_tcp_state_counts() -> dict[str, int]:
    """Get TCP connection state counts from /proc/net/tcp."""
    states = {
        "01": "established",
        "06": "time_wait",
        "08": "close_wait",
        "03": "syn_recv",
        "05": "fin_wait2",
        "0A": "listen",
    }
    counts: dict[str, int] = dict.fromkeys(states.values(), 0)
    try:
        with open("/proc/net/tcp") as f:
            next(f)  # Skip header
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    state = parts[3].upper()
                    if state in states:
                        counts[states[state]] += 1
    except (OSError, FileNotFoundError):
        pass
    return counts


def _get_tcp_retransmit_rate() -> float:
    """Get TCP retransmit rate from /proc/net/snmp."""
    try:
        with open("/proc/net/snmp") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith("Tcp:") and i + 1 < len(lines):
                headers = line.split()
                values = lines[i + 1].split()
                if len(headers) == len(values):
                    data = dict(zip(headers[1:], values[1:], strict=False))
                    out_segs = int(data.get("OutSegs", "0"))
                    retrans_segs = int(data.get("RetransSegs", "0"))
                    if out_segs > 0:
                        return round(retrans_segs / out_segs, 6)
                break
    except (OSError, FileNotFoundError, ValueError):
        pass
    return 0.0


def _get_zombie_count() -> int:
    """Get number of zombie processes."""
    count = 0
    try:
        proc_dir = Path("/proc")
        for entry in proc_dir.iterdir():
            if entry.name.isdigit():
                try:
                    stat_file = entry / "stat"
                    content = stat_file.read_text()
                    # State is the field after (comm) — find closing paren
                    close_paren = content.rfind(")")
                    if close_paren >= 0 and len(content) > close_paren + 2:
                        state = content[close_paren + 2]
                        if state == "Z":
                            count += 1
                except (OSError, PermissionError):
                    pass
    except Exception:
        pass
    return count


def _get_pending_reboot() -> bool:
    """Check if system reboot is required."""
    return Path("/var/run/reboot-required").exists()


def _get_cert_expiry() -> list[dict[str, Any]]:
    """Get TLS certificate expiry for local endpoints."""
    results = []
    endpoints = ["localhost:443", "localhost:8443"]
    for endpoint in endpoints:
        try:
            result = subprocess.run(
                [
                    "openssl", "s_client", "-connect", endpoint,
                    "-servername", endpoint.split(":")[0],
                ],
                input=b"",
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Extract expiry with openssl x509
                x509_result = subprocess.run(
                    ["openssl", "x509", "-noout", "-enddate"],
                    input=result.stdout,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if x509_result.returncode == 0:
                    date_str = x509_result.stdout.strip().replace("notAfter=", "")
                    try:
                        from email.utils import parsedate_to_datetime
                        expiry = parsedate_to_datetime(date_str)
                        days = (expiry - datetime.utcnow().replace(
                            tzinfo=expiry.tzinfo)).days
                        results.append({
                            "endpoint": endpoint,
                            "days_until_expiry": days,
                        })
                    except Exception:
                        pass
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return results


def _get_dns_latency() -> dict[str, float]:
    """Get DNS query latency percentiles from probed socket."""
    result: dict[str, float] = {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    try:
        import json as json_mod
        import socket as sock_mod
        s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect("/run/elle/probed.sock")
        s.sendall(b'{"query":"dns_latency"}\n')
        data = s.recv(4096)
        s.close()
        if data:
            parsed = json_mod.loads(data.decode())
            result = {
                "p50": parsed.get("p50_ms", 0.0),
                "p95": parsed.get("p95_ms", 0.0),
                "p99": parsed.get("p99_ms", 0.0),
            }
    except Exception:
        pass
    return result


def _get_cgroup_pressure() -> list[dict[str, Any]]:
    """Get cgroup memory pressure for system slices."""
    cgroups = []
    try:
        for base in ["/sys/fs/cgroup/system.slice", "/sys/fs/cgroup/user.slice"]:
            base_path = Path(base)
            if not base_path.exists():
                continue
            for entry in sorted(base_path.iterdir())[:20]:
                if not entry.is_dir():
                    continue
                mem_current = entry / "memory.current"
                mem_max = entry / "memory.max"
                if mem_current.exists() and mem_max.exists():
                    try:
                        current = int(mem_current.read_text().strip())
                        max_str = mem_max.read_text().strip()
                        if max_str == "max":
                            continue  # No limit set
                        max_val = int(max_str)
                        if max_val > 0:
                            pct = round((current / max_val) * 100, 2)
                            cgroups.append({
                                "name": entry.name,
                                "mem_current_mb": current // (1024 * 1024),
                                "mem_max_mb": max_val // (1024 * 1024),
                                "mem_pct": pct,
                            })
                    except (ValueError, OSError):
                        pass
    except Exception:
        pass
    # Sort by memory percentage descending, return top 10
    def _cgroup_sort_key(x: dict[str, Any]) -> float:
        val = x.get("mem_pct", 0)
        return float(val) if isinstance(val, (int, float)) else 0.0
    cgroups.sort(key=_cgroup_sort_key, reverse=True)
    return cgroups[:10]


def _get_psi_pressure() -> dict[str, float]:
    """Get PSI pressure averages from /proc/pressure/."""
    result: dict[str, float] = {
        "cpu_avg10": 0.0,
        "mem_avg10": 0.0,
        "io_avg10": 0.0,
    }
    for resource, key in [("cpu", "cpu_avg10"), ("memory", "mem_avg10"), ("io", "io_avg10")]:
        try:
            path = Path(f"/proc/pressure/{resource}")
            if path.exists():
                content = path.read_text()
                for line in content.split("\n"):
                    if line.startswith("some"):
                        # Parse: some avg10=X.XX avg60=X.XX avg300=X.XX total=XXXX
                        for part in line.split():
                            if part.startswith("avg10="):
                                result[key] = float(part.split("=")[1])
                                break
                        break
        except (OSError, ValueError):
            pass
    return result


def _get_security_events() -> int:
    """Get count of security-relevant events in last hour."""
    count = 0
    try:
        result = subprocess.run(
            [
                "journalctl", "-q", "--since", "1 hour ago",
                "-t", "sshd", "-t", "sudo", "-t", "polkitd",
                "--no-pager", "-o", "cat",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            count = len([l for l in result.stdout.strip().split("\n") if l])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return count


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
