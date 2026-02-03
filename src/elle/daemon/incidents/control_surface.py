"""Control surface snapshot - discrete, hashable configuration state.

Captures point-in-time system configuration that only changes when
someone explicitly modifies it. Used for drift detection and
configuration tracking.

Design principles:
- Control surfaces are hashable for drift detection
- Configuration only changes on admin action
- Capture known config roots, not all of /etc
- Everything must be cheap to collect
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Known Configuration Roots
# =============================================================================

# Config directories to track (not all of /etc)
KNOWN_CONFIG_ROOTS: tuple[str, ...] = (
    "/etc/ssh",
    "/etc/nginx",
    "/etc/systemd",
    "/etc/docker",
    "/etc/netplan",
    "/etc/sysctl.d",
    "/etc/apt",
    "/etc/sudoers.d",
)

# Key sysctls to track
KEY_SYSCTLS: tuple[str, ...] = (
    "vm.swappiness",
    "vm.overcommit_memory",
    "vm.overcommit_ratio",
    "net.ipv4.ip_forward",
    "net.ipv4.tcp_syncookies",
    "kernel.panic",
    "kernel.sysrq",
)


# =============================================================================
# Surface 1: Systemd Service Surface
# =============================================================================


class SystemdServiceSurface(BaseModel):
    """Configuration surface for a systemd service unit."""

    model_config = ConfigDict(frozen=True)

    unit_name: str = Field(description="Service unit name")
    enabled: bool = Field(default=False, description="Service is enabled")
    active_state: str = Field(default="unknown", description="Active state")
    restart_policy: str = Field(default="", description="Restart policy")
    user: str = Field(default="", description="User to run as")
    group: str = Field(default="", description="Group to run as")
    cpu_quota: str = Field(default="", description="CPU quota (e.g., '50%')")
    memory_max: str = Field(default="", description="Memory max (e.g., '1G')")
    dropin_count: int = Field(ge=0, default=0, description="Number of drop-in files")
    effective_hash: str = Field(default="", description="SHA256 of merged unit config")

    def surface_hash(self) -> str:
        """Compute hash of this service's configuration."""
        return (
            self.effective_hash
            or hashlib.sha256(
                f"{self.enabled}:{self.restart_policy}:{self.user}:{self.group}:{self.cpu_quota}:{self.memory_max}".encode()
            ).hexdigest()[:16]
        )


# =============================================================================
# Surface 2: Configuration File Surface
# =============================================================================


class ConfigFileSurface(BaseModel):
    """Configuration surface for a config file or directory."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="File or directory path")
    owner: str = Field(default="", description="Owner (user:group)")
    mode: str = Field(default="", description="File mode (octal)")
    sha256: str = Field(default="", description="SHA256 of file contents")
    fragment_count: int = Field(ge=0, default=0, description="*.d/ directory fragment count")
    mtime: datetime | None = Field(default=None, description="Modification time")

    def surface_hash(self) -> str:
        """Compute hash of this config's state."""
        return self.sha256[:16] if self.sha256 else ""


# =============================================================================
# Surface 3: Package Surface
# =============================================================================


class PackageInfo(BaseModel):
    """Package version information."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Package name")
    version: str = Field(description="Installed version")
    auto_installed: bool = Field(default=False, description="Automatically installed")


class PackageSurface(BaseModel):
    """Configuration surface for installed packages."""

    model_config = ConfigDict(frozen=True)

    packages: tuple[PackageInfo, ...] = Field(default_factory=tuple, description="Tracked packages")
    surface_hash_value: str = Field(default="", description="SHA256 of sorted name:version pairs")

    def surface_hash(self) -> str:
        """Compute hash of package state."""
        if self.surface_hash_value:
            return self.surface_hash_value[:16]
        pairs = sorted(f"{p.name}:{p.version}" for p in self.packages)
        return hashlib.sha256("\n".join(pairs).encode()).hexdigest()[:16]


# =============================================================================
# Surface 4: Scheduler & Automation Surface
# =============================================================================


class SchedulerSurface(BaseModel):
    """Configuration surface for scheduled tasks."""

    model_config = ConfigDict(frozen=True)

    systemd_timers_hash: str = Field(default="", description="Hash of all *.timer units")
    cron_entries_hash: str = Field(default="", description="Hash of /etc/cron.*")
    unattended_upgrades: bool = Field(default=False, description="Unattended upgrades enabled")

    def surface_hash(self) -> str:
        """Compute combined scheduler hash."""
        combined = f"{self.systemd_timers_hash}:{self.cron_entries_hash}:{self.unattended_upgrades}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]


# =============================================================================
# Surface 5: Privilege & Policy Surface
# =============================================================================


class PrivilegeSurface(BaseModel):
    """Configuration surface for privilege and security policies."""

    model_config = ConfigDict(frozen=True)

    sudoers_hash: str = Field(default="", description="SHA256 of sudoers + sudoers.d/*")
    groups_hash: str = Field(default="", description="SHA256 of /etc/group")
    polkit_rules_hash: str = Field(default="", description="SHA256 of polkit rules")
    selinux_mode: str = Field(default="disabled", description="SELinux mode")
    apparmor_mode: str = Field(default="unknown", description="AppArmor mode")

    def surface_hash(self) -> str:
        """Compute combined privilege hash."""
        combined = (
            f"{self.sudoers_hash}:{self.groups_hash}:{self.polkit_rules_hash}:{self.selinux_mode}:{self.apparmor_mode}"
        )
        return hashlib.sha256(combined.encode()).hexdigest()[:16]


# =============================================================================
# Surface 6: Network Control Plane Surface
# =============================================================================


class NetworkControlSurface(BaseModel):
    """Configuration surface for network control plane."""

    model_config = ConfigDict(frozen=True)

    network_manager: str = Field(default="unknown", description="Active network manager (NM/netplan/networkd)")
    firewall_frontend: str = Field(default="none", description="Active firewall (ufw/firewalld/nftables)")
    resolver_mode: str = Field(default="unknown", description="Resolver mode (systemd-resolved/static)")

    def surface_hash(self) -> str:
        """Compute network control plane hash."""
        return hashlib.sha256(
            f"{self.network_manager}:{self.firewall_frontend}:{self.resolver_mode}".encode()
        ).hexdigest()[:16]


# =============================================================================
# Surface 7: Kernel & Resource Policy Surface
# =============================================================================


class KernelPolicySurface(BaseModel):
    """Configuration surface for kernel and resource policies."""

    model_config = ConfigDict(frozen=True)

    kernel_version: str = Field(default="", description="Running kernel version")
    cgroup_mode: str = Field(default="unknown", description="Cgroup mode (v1/v2/hybrid)")
    key_sysctls: dict[str, str] = Field(default_factory=dict, description="Key sysctl values")
    swap_enabled: bool = Field(default=False, description="Swap is enabled")
    overcommit_mode: str = Field(default="", description="vm.overcommit_memory value")

    def surface_hash(self) -> str:
        """Compute kernel policy hash."""
        sysctl_str = ":".join(f"{k}={v}" for k, v in sorted(self.key_sysctls.items()))
        combined = f"{self.kernel_version}:{self.cgroup_mode}:{sysctl_str}:{self.swap_enabled}:{self.overcommit_mode}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]


# =============================================================================
# Surface 8: Hardware Identity Surface
# =============================================================================


class HardwareIdentitySurface(BaseModel):
    """Configuration surface for hardware identity."""

    model_config = ConfigDict(frozen=True)

    disk_layout_hash: str = Field(default="", description="SHA256 of lsblk topology")
    filesystem_types: tuple[str, ...] = Field(default_factory=tuple, description="Filesystem types in use")
    total_memory_mb: int = Field(ge=0, default=0, description="Total system memory")
    cpu_cores: int = Field(ge=0, default=0, description="Number of CPU cores")
    virtualization: str = Field(default="none", description="Virtualization type")

    def surface_hash(self) -> str:
        """Compute hardware identity hash."""
        combined = f"{self.disk_layout_hash}:{':'.join(sorted(self.filesystem_types))}:{self.total_memory_mb}:{self.cpu_cores}:{self.virtualization}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]


# =============================================================================
# Complete Control Surface Snapshot
# =============================================================================


class ControlSurfaceSnapshot(BaseModel):
    """Complete control surface snapshot - what's configured.

    Captures point-in-time system configuration that only changes
    when an admin makes changes. Used for drift detection.

    Typical size: 5-15KB JSON
    Collection time: <400ms cold, <100ms warm (with caching)
    """

    model_config = ConfigDict(frozen=True)

    # Surfaces
    services: tuple[SystemdServiceSurface, ...] = Field(
        default_factory=tuple,
        description="Service configurations (implicated services only)",
    )
    configs: tuple[ConfigFileSurface, ...] = Field(
        default_factory=tuple,
        description="Config file states",
    )
    packages: PackageSurface = Field(default_factory=PackageSurface)
    scheduler: SchedulerSurface = Field(default_factory=SchedulerSurface)
    privilege: PrivilegeSurface = Field(default_factory=PrivilegeSurface)
    network_control: NetworkControlSurface = Field(default_factory=NetworkControlSurface)
    kernel_policy: KernelPolicySurface = Field(default_factory=KernelPolicySurface)
    hardware: HardwareIdentitySurface = Field(default_factory=HardwareIdentitySurface)

    # Metadata
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def surface_hashes(self) -> dict[str, str]:
        """Compute hashes for all surfaces.

        Returns:
            Dict mapping surface name to its hash.
            Compare hashes between two snapshots to detect drift.
        """
        hashes = {
            "packages": self.packages.surface_hash(),
            "scheduler": self.scheduler.surface_hash(),
            "privilege": self.privilege.surface_hash(),
            "network_control": self.network_control.surface_hash(),
            "kernel_policy": self.kernel_policy.surface_hash(),
            "hardware": self.hardware.surface_hash(),
        }

        # Add service hashes
        for svc in self.services:
            hashes[f"service:{svc.unit_name}"] = svc.surface_hash()

        # Add config hashes
        for cfg in self.configs:
            hashes[f"config:{cfg.path}"] = cfg.surface_hash()

        return hashes


# =============================================================================
# Collection Functions
# =============================================================================


def _hash_file(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    try:
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except Exception:
        return ""


def _hash_directory(path: Path) -> str:
    """Compute SHA256 hash of all files in a directory."""
    try:
        if not path.is_dir():
            return ""

        contents: list[str] = []
        for f in sorted(path.rglob("*")):
            if f.is_file():
                file_hash = _hash_file(f)
                contents.append(f"{f.relative_to(path)}:{file_hash}")

        return hashlib.sha256("\n".join(contents).encode()).hexdigest()
    except Exception:
        return ""


def _collect_service_surface(service: str) -> SystemdServiceSurface | None:
    """Collect configuration surface for a systemd service."""
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service,
                "--property=UnitFileState,ActiveState,Restart,User,Group,CPUQuota,MemoryMax",
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

        # Check if enabled
        enabled = props.get("UnitFileState", "") in ("enabled", "enabled-runtime")

        # Count drop-in files
        dropin_count = 0
        dropin_dir = Path(f"/etc/systemd/system/{service}.d")
        if dropin_dir.is_dir():
            dropin_count = len(list(dropin_dir.glob("*.conf")))

        # Compute effective hash from systemctl cat
        effective_hash = ""
        try:
            cat_result = subprocess.run(
                ["systemctl", "cat", service],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if cat_result.returncode == 0:
                effective_hash = hashlib.sha256(cat_result.stdout.encode()).hexdigest()
        except Exception:
            pass

        return SystemdServiceSurface(
            unit_name=service,
            enabled=enabled,
            active_state=props.get("ActiveState", "unknown"),
            restart_policy=props.get("Restart", ""),
            user=props.get("User", ""),
            group=props.get("Group", ""),
            cpu_quota=props.get("CPUQuota", ""),
            memory_max=props.get("MemoryMax", ""),
            dropin_count=dropin_count,
            effective_hash=effective_hash,
        )
    except Exception:
        return None


def _collect_config_surface(path: str) -> ConfigFileSurface | None:
    """Collect configuration surface for a config file or directory."""
    p = Path(path)
    if not p.exists():
        return None

    try:
        stat = p.stat()
        owner = f"{stat.st_uid}:{stat.st_gid}"
        mode = oct(stat.st_mode)[-3:]
        mtime = datetime.fromtimestamp(stat.st_mtime)

        if p.is_file():
            sha256 = _hash_file(p)
            fragment_count = 0
        else:
            sha256 = _hash_directory(p)
            # Count *.d fragments
            fragment_count = 0
            d_dir = Path(f"{path}.d") if not path.endswith(".d") else p
            if d_dir.is_dir():
                fragment_count = len(list(d_dir.glob("*")))

        return ConfigFileSurface(
            path=path,
            owner=owner,
            mode=mode,
            sha256=sha256,
            fragment_count=fragment_count,
            mtime=mtime,
        )
    except Exception:
        return None


def _collect_package_surface(packages: list[str] | None = None) -> PackageSurface:
    """Collect package surface."""
    pkg_infos: list[PackageInfo] = []

    # Default bedrock packages if none specified
    if not packages:
        packages = [
            "systemd",
            "linux-image-generic",
            "docker.io",
            "nginx",
            "openssh-server",
            "python3",
        ]

    try:
        for pkg in packages[:50]:  # Limit to 50 packages
            result = subprocess.run(
                ["dpkg-query", "-W", "-f=${Version}", pkg],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Check if auto-installed
                auto = False
                try:
                    auto_result = subprocess.run(
                        ["apt-mark", "showauto", pkg],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    auto = pkg in auto_result.stdout
                except Exception:
                    pass

                pkg_infos.append(
                    PackageInfo(
                        name=pkg,
                        version=result.stdout.strip(),
                        auto_installed=auto,
                    )
                )
    except Exception:
        pass

    # Compute surface hash
    pairs = sorted(f"{p.name}:{p.version}" for p in pkg_infos)
    surface_hash = hashlib.sha256("\n".join(pairs).encode()).hexdigest()

    return PackageSurface(
        packages=tuple(pkg_infos),
        surface_hash_value=surface_hash,
    )


def _collect_scheduler_surface() -> SchedulerSurface:
    """Collect scheduler and automation surface."""
    # Hash systemd timers
    timers_hash = ""
    try:
        result = subprocess.run(
            ["systemctl", "list-timers", "--no-pager", "--plain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            timers_hash = hashlib.sha256(result.stdout.encode()).hexdigest()
    except Exception:
        pass

    # Hash cron entries
    cron_hash = ""
    cron_dirs = ["/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly", "/etc/cron.weekly", "/etc/cron.monthly"]
    cron_contents: list[str] = []
    for d in cron_dirs:
        p = Path(d)
        if p.is_dir():
            for f in sorted(p.glob("*")):
                if f.is_file():
                    cron_contents.append(f"{f.name}:{_hash_file(f)}")
    if cron_contents:
        cron_hash = hashlib.sha256("\n".join(cron_contents).encode()).hexdigest()

    # Check unattended upgrades
    unattended = Path("/etc/apt/apt.conf.d/20auto-upgrades").exists()

    return SchedulerSurface(
        systemd_timers_hash=timers_hash,
        cron_entries_hash=cron_hash,
        unattended_upgrades=unattended,
    )


def _collect_privilege_surface() -> PrivilegeSurface:
    """Collect privilege and policy surface."""
    # Hash sudoers
    sudoers_hash = ""
    sudoers_contents: list[str] = []
    sudoers_path = Path("/etc/sudoers")
    if sudoers_path.exists():
        sudoers_contents.append(f"sudoers:{_hash_file(sudoers_path)}")
    sudoers_d = Path("/etc/sudoers.d")
    if sudoers_d.is_dir():
        for f in sorted(sudoers_d.glob("*")):
            if f.is_file():
                sudoers_contents.append(f"{f.name}:{_hash_file(f)}")
    if sudoers_contents:
        sudoers_hash = hashlib.sha256("\n".join(sudoers_contents).encode()).hexdigest()

    # Hash groups
    groups_hash = _hash_file(Path("/etc/group"))

    # Hash polkit rules
    polkit_hash = ""
    polkit_dir = Path("/etc/polkit-1/rules.d")
    if polkit_dir.is_dir():
        polkit_hash = _hash_directory(polkit_dir)

    # SELinux mode
    selinux_mode = "disabled"
    try:
        result = subprocess.run(["getenforce"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            selinux_mode = result.stdout.strip().lower()
    except Exception:
        pass

    # AppArmor mode
    apparmor_mode = "unknown"
    try:
        result = subprocess.run(["aa-status", "--enabled"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            apparmor_mode = "enabled"
        else:
            apparmor_mode = "disabled"
    except Exception:
        pass

    return PrivilegeSurface(
        sudoers_hash=sudoers_hash,
        groups_hash=groups_hash,
        polkit_rules_hash=polkit_hash,
        selinux_mode=selinux_mode,
        apparmor_mode=apparmor_mode,
    )


def _collect_network_control_surface() -> NetworkControlSurface:
    """Collect network control plane surface."""
    # Detect network manager
    network_manager = "unknown"
    if Path("/run/NetworkManager/NetworkManager.pid").exists():
        network_manager = "NetworkManager"
    elif Path("/run/systemd/netif").exists():
        network_manager = "systemd-networkd"
    elif Path("/etc/netplan").is_dir() and list(Path("/etc/netplan").glob("*.yaml")):
        network_manager = "netplan"

    # Detect firewall
    firewall_frontend = "none"
    try:
        # Check ufw
        result = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0 and "Status: active" in result.stdout:
            firewall_frontend = "ufw"
    except Exception:
        pass

    if firewall_frontend == "none":
        try:
            # Check firewalld
            result = subprocess.run(["firewall-cmd", "--state"], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and "running" in result.stdout:
                firewall_frontend = "firewalld"
        except Exception:
            pass

    if firewall_frontend == "none":
        # Check nftables
        if Path("/etc/nftables.conf").exists():
            firewall_frontend = "nftables"

    # Detect resolver
    resolver_mode = "unknown"
    resolv_conf = Path("/etc/resolv.conf")
    if resolv_conf.is_symlink():
        target = str(resolv_conf.resolve())
        if "systemd" in target:
            resolver_mode = "systemd-resolved"
        else:
            resolver_mode = "symlinked"
    elif resolv_conf.exists():
        resolver_mode = "static"

    return NetworkControlSurface(
        network_manager=network_manager,
        firewall_frontend=firewall_frontend,
        resolver_mode=resolver_mode,
    )


def _collect_kernel_policy_surface() -> KernelPolicySurface:
    """Collect kernel and resource policy surface."""
    import platform

    kernel_version = platform.release()

    # Detect cgroup mode
    cgroup_mode = "unknown"
    if Path("/sys/fs/cgroup/cgroup.controllers").exists():
        cgroup_mode = "v2"
    elif Path("/sys/fs/cgroup/memory").exists():
        cgroup_mode = "v1"
    elif Path("/sys/fs/cgroup").exists():
        cgroup_mode = "hybrid"

    # Collect key sysctls
    key_sysctls: dict[str, str] = {}
    for sysctl in KEY_SYSCTLS:
        sysctl_path = Path(f"/proc/sys/{sysctl.replace('.', '/')}")
        try:
            if sysctl_path.exists():
                key_sysctls[sysctl] = sysctl_path.read_text().strip()
        except Exception:
            pass

    # Check swap
    swap_enabled = False
    try:
        swaps = Path("/proc/swaps").read_text()
        swap_enabled = len(swaps.strip().split("\n")) > 1
    except Exception:
        pass

    overcommit_mode = key_sysctls.get("vm.overcommit_memory", "")

    return KernelPolicySurface(
        kernel_version=kernel_version,
        cgroup_mode=cgroup_mode,
        key_sysctls=key_sysctls,
        swap_enabled=swap_enabled,
        overcommit_mode=overcommit_mode,
    )


def _collect_hardware_identity_surface() -> HardwareIdentitySurface:
    """Collect hardware identity surface."""
    # Disk layout hash
    disk_layout_hash = ""
    try:
        result = subprocess.run(
            ["lsblk", "-J"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            disk_layout_hash = hashlib.sha256(result.stdout.encode()).hexdigest()
    except Exception:
        pass

    # Filesystem types
    fs_types: set[str] = set()
    try:
        result = subprocess.run(
            ["mount", "-l", "-t", "ext4,xfs,btrfs,zfs,ntfs,vfat"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                parts = line.split()
                if len(parts) >= 5:
                    fs_types.add(parts[4])
    except Exception:
        pass

    # Total memory
    total_memory_mb = 0
    try:
        content = Path("/proc/meminfo").read_text()
        for line in content.split("\n"):
            if line.startswith("MemTotal:"):
                total_memory_mb = int(line.split()[1]) // 1024
                break
    except Exception:
        pass

    # CPU cores
    cpu_cores = os.cpu_count() or 0

    # Virtualization
    virtualization = "none"
    try:
        result = subprocess.run(
            ["systemd-detect-virt"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            virt = result.stdout.strip()
            if virt and virt != "none":
                virtualization = virt
    except Exception:
        pass

    return HardwareIdentitySurface(
        disk_layout_hash=disk_layout_hash,
        filesystem_types=tuple(sorted(fs_types)),
        total_memory_mb=total_memory_mb,
        cpu_cores=cpu_cores,
        virtualization=virtualization,
    )


def collect_control_surface(
    services: list[str] | None = None,
    config_paths: list[str] | None = None,
    packages: list[str] | None = None,
) -> ControlSurfaceSnapshot:
    """Collect a complete control surface snapshot.

    Gathers point-in-time system configuration for drift detection.
    Designed to complete in <400ms cold, <100ms warm (with caching).

    Args:
        services: Service names to collect surface for.
        config_paths: Config file/directory paths to collect.
        packages: Package names to collect.

    Returns:
        ControlSurfaceSnapshot with current configuration state.
    """
    # Collect service surfaces
    service_surfaces: list[SystemdServiceSurface] = []
    if services:
        for svc in services[:20]:  # Limit to 20 services
            surface = _collect_service_surface(svc)
            if surface:
                service_surfaces.append(surface)

    # Collect config surfaces
    config_surfaces: list[ConfigFileSurface] = []
    paths_to_check = list(config_paths or []) + list(KNOWN_CONFIG_ROOTS)
    for path in paths_to_check[:30]:  # Limit to 30 paths
        cfg_surface = _collect_config_surface(path)
        if cfg_surface:
            config_surfaces.append(cfg_surface)

    return ControlSurfaceSnapshot(
        services=tuple(service_surfaces),
        configs=tuple(config_surfaces),
        packages=_collect_package_surface(packages),
        scheduler=_collect_scheduler_surface(),
        privilege=_collect_privilege_surface(),
        network_control=_collect_network_control_surface(),
        kernel_policy=_collect_kernel_policy_surface(),
        hardware=_collect_hardware_identity_surface(),
        collected_at=datetime.now(timezone.utc),
    )
