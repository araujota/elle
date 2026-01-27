"""Semantic diff generation for incident snapshots.

Generates human-readable summaries of system changes between
two points in time, rather than raw diffs.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from elle.daemon.incidents.models import (
    ChangeSignificance,
    ConfigFileState,
    SemanticChange,
    SemanticDiff,
    SystemSnapshot,
)

logger = logging.getLogger(__name__)


def generate_semantic_diff(
    before: SystemSnapshot,
    after: SystemSnapshot,
    config_before: tuple[ConfigFileState, ...] = (),
    config_after: tuple[ConfigFileState, ...] = (),
) -> SemanticDiff:
    """Generate a human-readable diff between two snapshots.

    Analyzes system state changes and produces semantic descriptions
    rather than raw diffs.

    Args:
        before: System state before changes.
        after: System state after changes.
        config_before: Config file states before changes.
        config_after: Config file states after changes.

    Returns:
        SemanticDiff with human-readable change descriptions.
    """
    changes: list[SemanticChange] = []

    # Kernel changes
    kernel_changes = _diff_kernel(before, after)
    changes.extend(kernel_changes)

    # Memory changes
    memory_changes = _diff_memory(before, after)
    changes.extend(memory_changes)

    # Disk changes
    disk_changes = _diff_disks(before.disks, after.disks)
    changes.extend(disk_changes)

    # Service changes
    service_changes = _diff_services(before.services, after.services)
    changes.extend(service_changes)

    # Network interface changes
    network_changes = _diff_interfaces(before.interfaces, after.interfaces)
    changes.extend(network_changes)

    # Docker changes
    docker_changes = _diff_docker(before, after)
    changes.extend(docker_changes)

    # Package changes
    package_changes = _diff_packages(before, after)
    changes.extend(package_changes)

    # Config file changes
    config_changes = _diff_configs(config_before, config_after)
    changes.extend(config_changes)

    return SemanticDiff(
        from_time=before.collected_at,
        to_time=after.collected_at,
        changes=tuple(changes),
    )


def _diff_kernel(
    before: SystemSnapshot,
    after: SystemSnapshot,
) -> list[SemanticChange]:
    """Detect kernel version changes."""
    changes = []

    if before.kernel != after.kernel:
        changes.append(
            SemanticChange(
                category="kernel",
                description=f"Kernel updated from {before.kernel} to {after.kernel}",
                technical_detail=f"uname -r: {before.kernel} -> {after.kernel}",
                significance="high",
            )
        )

    return changes


def _diff_memory(
    before: SystemSnapshot,
    after: SystemSnapshot,
) -> list[SemanticChange]:
    """Detect significant memory changes."""
    changes = []

    # Check for major swap usage change
    if before.swap_total_mb > 0 and after.swap_total_mb > 0:
        before_pct = before.swap_used_mb / before.swap_total_mb
        after_pct = after.swap_used_mb / after.swap_total_mb

        # Alert if swap usage increased significantly
        if after_pct > before_pct + 0.2:
            changes.append(
                SemanticChange(
                    category="kernel",
                    description=f"Swap usage increased from {int(before_pct * 100)}% to {int(after_pct * 100)}%",
                    technical_detail=f"swap_used_mb: {before.swap_used_mb} -> {after.swap_used_mb}",
                    significance="medium",
                )
            )

    return changes


def _diff_disks(
    before: tuple[dict[str, Any], ...],
    after: tuple[dict[str, Any], ...],
) -> list[SemanticChange]:
    """Detect disk usage changes."""
    changes = []

    # Index by mount point
    before_by_mount = {d.get("mount"): d for d in before}
    after_by_mount = {d.get("mount"): d for d in after}

    for mount, after_disk in after_by_mount.items():
        if not mount:
            continue

        before_disk = before_by_mount.get(mount)
        if not before_disk:
            # New mount appeared
            changes.append(
                SemanticChange(
                    category="config",
                    description=f"New mount point: {mount}",
                    technical_detail=f"device: {after_disk.get('device', 'unknown')}",
                    significance="medium",
                )
            )
            continue

        # Check for disk usage changes
        before_pct = before_disk.get("used_pct", 0)
        after_pct = after_disk.get("used_pct", 0)

        if after_pct >= 90 and before_pct < 90:
            changes.append(
                SemanticChange(
                    category="config",
                    description=f"Disk {mount} now at {after_pct}% capacity",
                    technical_detail=f"used_pct: {before_pct}% -> {after_pct}%",
                    significance="high",
                )
            )
        elif after_pct - before_pct >= 10:
            changes.append(
                SemanticChange(
                    category="config",
                    description=f"Disk usage on {mount} increased by {after_pct - before_pct}%",
                    technical_detail=f"used_pct: {before_pct}% -> {after_pct}%",
                    significance="medium",
                )
            )

    # Check for removed mounts
    for mount in before_by_mount:
        if mount and mount not in after_by_mount:
            changes.append(
                SemanticChange(
                    category="config",
                    description=f"Mount point removed: {mount}",
                    significance="medium",
                )
            )

    return changes


def _diff_services(
    before: tuple[dict[str, Any], ...],
    after: tuple[dict[str, Any], ...],
) -> list[SemanticChange]:
    """Detect systemd service state changes."""
    changes = []

    # Index by service name
    before_by_name = {s.get("name"): s for s in before}
    after_by_name = {s.get("name"): s for s in after}

    for name, after_svc in after_by_name.items():
        if not name:
            continue

        before_svc = before_by_name.get(name)
        after_active = after_svc.get("active", False)
        after_failed = after_svc.get("failed", False)

        if not before_svc:
            # New service appeared
            if after_active:
                changes.append(
                    SemanticChange(
                        category="service",
                        description=f"Service {name} started",
                        significance="medium",
                    )
                )
            continue

        before_active = before_svc.get("active", False)
        before_failed = before_svc.get("failed", False)

        # Service started
        if after_active and not before_active:
            changes.append(
                SemanticChange(
                    category="service",
                    description=f"Service {name} started",
                    significance="medium",
                )
            )

        # Service stopped
        elif not after_active and before_active:
            changes.append(
                SemanticChange(
                    category="service",
                    description=f"Service {name} stopped",
                    significance="medium",
                )
            )

        # Service failed
        elif after_failed and not before_failed:
            changes.append(
                SemanticChange(
                    category="service",
                    description=f"Service {name} entered failed state",
                    significance="high",
                )
            )

        # Service recovered
        elif not after_failed and before_failed:
            changes.append(
                SemanticChange(
                    category="service",
                    description=f"Service {name} recovered from failed state",
                    significance="medium",
                )
            )

    return changes


def _diff_interfaces(
    before: tuple[dict[str, Any], ...],
    after: tuple[dict[str, Any], ...],
) -> list[SemanticChange]:
    """Detect network interface state changes."""
    changes = []

    # Index by interface name
    before_by_name = {i.get("name"): i for i in before}
    after_by_name = {i.get("name"): i for i in after}

    for name, after_iface in after_by_name.items():
        if not name or name == "lo":
            continue

        before_iface = before_by_name.get(name)
        after_state = after_iface.get("state", "unknown")

        if not before_iface:
            # New interface appeared
            changes.append(
                SemanticChange(
                    category="network",
                    description=f"Network interface {name} added",
                    technical_detail=f"state: {after_state}, ip: {after_iface.get('ip', 'none')}",
                    significance="medium",
                )
            )
            continue

        before_state = before_iface.get("state", "unknown")

        # State change
        if after_state != before_state:
            if after_state == "up" and before_state == "down":
                changes.append(
                    SemanticChange(
                        category="network",
                        description=f"Network interface {name} came up",
                        significance="medium",
                    )
                )
            elif after_state == "down" and before_state == "up":
                changes.append(
                    SemanticChange(
                        category="network",
                        description=f"Network interface {name} went down",
                        significance="high",
                    )
                )

        # IP address change
        before_ip = before_iface.get("ip")
        after_ip = after_iface.get("ip")
        if before_ip != after_ip:
            changes.append(
                SemanticChange(
                    category="network",
                    description=f"IP address changed on {name}",
                    technical_detail=f"ip: {before_ip} -> {after_ip}",
                    significance="medium",
                )
            )

    return changes


def _diff_docker(
    before: SystemSnapshot,
    after: SystemSnapshot,
) -> list[SemanticChange]:
    """Detect Docker container changes."""
    changes = []

    # Container count changes
    if after.docker_running != before.docker_running:
        diff = after.docker_running - before.docker_running
        if diff > 0:
            changes.append(
                SemanticChange(
                    category="service",
                    description=f"{diff} Docker container(s) started",
                    technical_detail=f"running: {before.docker_running} -> {after.docker_running}",
                    significance="medium",
                )
            )
        else:
            changes.append(
                SemanticChange(
                    category="service",
                    description=f"{-diff} Docker container(s) stopped",
                    technical_detail=f"running: {before.docker_running} -> {after.docker_running}",
                    significance="medium",
                )
            )

    # Exited container changes
    if after.docker_exited > before.docker_exited:
        diff = after.docker_exited - before.docker_exited
        changes.append(
            SemanticChange(
                category="service",
                description=f"{diff} Docker container(s) exited",
                technical_detail=f"exited: {before.docker_exited} -> {after.docker_exited}",
                significance="medium",
            )
        )

    return changes


def _diff_packages(
    before: SystemSnapshot,
    after: SystemSnapshot,
) -> list[SemanticChange]:
    """Detect package version changes between snapshots.

    Compares package versions and detects upgrades, installations,
    and removals.

    Args:
        before: System state before changes.
        after: System state after changes.

    Returns:
        List of SemanticChange for package differences.
    """
    changes = []

    # Build lookup dicts
    pre_pkgs = {p.name: p.version for p in before.packages}
    post_pkgs = {p.name: p.version for p in after.packages}

    # Track bedrock status for significance
    pre_bedrock = {p.name for p in before.packages if p.is_bedrock}
    post_bedrock = {p.name for p in after.packages if p.is_bedrock}

    # Upgrades / version changes
    for name, post_ver in post_pkgs.items():
        pre_ver = pre_pkgs.get(name)
        if pre_ver and pre_ver != post_ver:
            is_bedrock = name in pre_bedrock or name in post_bedrock
            changes.append(
                SemanticChange(
                    category="package",
                    description=f"{name} upgraded: {pre_ver} → {post_ver}",
                    technical_detail=f"dpkg-query -W {name}",
                    significance="high" if is_bedrock else "medium",
                )
            )

    # New installations
    for name in set(post_pkgs) - set(pre_pkgs):
        is_bedrock = name in post_bedrock
        changes.append(
            SemanticChange(
                category="package",
                description=f"{name} installed: {post_pkgs[name]}",
                technical_detail=f"apt install {name}",
                significance="high" if is_bedrock else "low",
            )
        )

    # Removals
    for name in set(pre_pkgs) - set(post_pkgs):
        is_bedrock = name in pre_bedrock
        changes.append(
            SemanticChange(
                category="package",
                description=f"{name} removed (was {pre_pkgs[name]})",
                technical_detail=f"apt remove {name}",
                significance="high" if is_bedrock else "medium",
            )
        )

    return changes


def _diff_configs(
    before: tuple[ConfigFileState, ...],
    after: tuple[ConfigFileState, ...],
) -> list[SemanticChange]:
    """Detect config file changes."""
    changes = []

    # Index by path
    before_by_path = {c.path: c for c in before}
    after_by_path = {c.path: c for c in after}

    for path, after_cfg in after_by_path.items():
        before_cfg = before_by_path.get(path)

        if not before_cfg:
            # New config file tracked
            changes.append(
                SemanticChange(
                    category="config",
                    description=f"Config file modified: {_path_basename(path)}",
                    technical_detail=path,
                    significance="medium",
                )
            )
            continue

        # Check for hash changes
        if before_cfg.sha256_after and after_cfg.sha256_after and before_cfg.sha256_after != after_cfg.sha256_after:
            # Determine significance based on path
            significance = _config_significance(path)
            changes.append(
                SemanticChange(
                    category="config",
                    description=f"Config file modified: {_path_basename(path)}",
                    technical_detail=path,
                    significance=significance,
                )
            )

    return changes


def _path_basename(path: str) -> str:
    """Get the basename of a path for display."""
    return Path(path).name


def _config_significance(path: str) -> ChangeSignificance:
    """Determine significance of a config file change."""
    high_significance = [
        "/etc/ssh/sshd_config",
        "/etc/sudoers",
        "/etc/pam.d/",
        "/etc/security/",
        "/etc/shadow",
        "/etc/passwd",
    ]

    medium_significance = [
        "/etc/fstab",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/netplan/",
        "/etc/systemd/",
    ]

    for prefix in high_significance:
        if path.startswith(prefix):
            return "high"

    for prefix in medium_significance:
        if path.startswith(prefix):
            return "medium"

    return "low"


def compute_file_hash(path: str | Path) -> str | None:
    """Compute SHA256 hash of a file.

    Args:
        path: Path to the file.

    Returns:
        SHA256 hex digest, or None if file doesn't exist.
    """
    path = Path(path)
    if not path.exists():
        return None

    try:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (OSError, PermissionError) as e:
        logger.warning(f"Failed to hash {path}: {e}")
        return None


def build_config_state(
    path: str | Path,
    sha256_before: str | None = None,
    backup_path: str | None = None,
) -> ConfigFileState:
    """Build a ConfigFileState for a file.

    Args:
        path: Path to the config file.
        sha256_before: Hash before changes (if known).
        backup_path: Path to backup copy (if exists).

    Returns:
        ConfigFileState with current file metadata.
    """
    path = Path(path)

    if not path.exists():
        return ConfigFileState(
            path=str(path),
            sha256_before=sha256_before,
            sha256_after=None,
            size_bytes=0,
            mtime=None,
            backup_path=backup_path,
        )

    stat = path.stat()
    sha256_after = compute_file_hash(path)

    return ConfigFileState(
        path=str(path),
        sha256_before=sha256_before,
        sha256_after=sha256_after,
        size_bytes=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime),
        backup_path=backup_path,
    )
