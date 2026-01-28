"""Surface drift detection.

Detects configuration drift between two control surface snapshots.
Given two snapshots, identifies which surfaces changed and provides
human-readable explanations.

The Payoff: Given two incidents, `surface_hashes(A) != surface_hashes(B)`
immediately tells you WHICH surface drifted, enabling targeted investigation.
"""

from __future__ import annotations

from dataclasses import dataclass

from elle.daemon.incidents.control_surface import ControlSurfaceSnapshot


@dataclass
class DriftDetail:
    """Details about a specific drift."""

    surface: str
    hash_before: str
    hash_after: str
    explanation: str


def detect_surface_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> dict[str, bool]:
    """Detect which surfaces drifted between two snapshots.

    Compares surface hashes between two snapshots to identify
    which configuration surfaces changed.

    Args:
        before: Earlier snapshot.
        after: Later snapshot.

    Returns:
        Dict mapping surface name to whether it drifted (True = changed).
    """
    before_hashes = before.surface_hashes()
    after_hashes = after.surface_hashes()

    # Get all unique surface names
    all_surfaces = set(before_hashes.keys()) | set(after_hashes.keys())

    drift: dict[str, bool] = {}
    for surface in all_surfaces:
        before_hash = before_hashes.get(surface, "")
        after_hash = after_hashes.get(surface, "")

        # Surface drifted if:
        # 1. Hash changed
        # 2. Surface appeared (was not in before)
        # 3. Surface disappeared (not in after)
        drifted = before_hash != after_hash
        drift[surface] = drifted

    return drift


def get_drifted_surfaces(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> list[str]:
    """Get list of surface names that drifted.

    Args:
        before: Earlier snapshot.
        after: Later snapshot.

    Returns:
        List of surface names that changed.
    """
    drift = detect_surface_drift(before, after)
    return [surface for surface, drifted in drift.items() if drifted]


def explain_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
    drifted_surfaces: list[str] | None = None,
) -> list[DriftDetail]:
    """Generate human-readable explanations for drifted surfaces.

    Args:
        before: Earlier snapshot.
        after: Later snapshot.
        drifted_surfaces: Optional list of surfaces to explain.
                         If None, detects and explains all drifted surfaces.

    Returns:
        List of DriftDetail with explanations for each drifted surface.
    """
    if drifted_surfaces is None:
        drifted_surfaces = get_drifted_surfaces(before, after)

    before_hashes = before.surface_hashes()
    after_hashes = after.surface_hashes()

    details: list[DriftDetail] = []
    for surface in drifted_surfaces:
        hash_before = before_hashes.get(surface, "")
        hash_after = after_hashes.get(surface, "")

        explanation = _explain_surface_drift(surface, before, after)

        details.append(
            DriftDetail(
                surface=surface,
                hash_before=hash_before,
                hash_after=hash_after,
                explanation=explanation,
            )
        )

    return details


def _explain_surface_drift(
    surface: str,
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Generate explanation for a specific surface drift."""
    # Service surface
    if surface.startswith("service:"):
        service_name = surface.split(":", 1)[1]
        return _explain_service_drift(service_name, before, after)

    # Config surface
    if surface.startswith("config:"):
        config_path = surface.split(":", 1)[1]
        return _explain_config_drift(config_path, before, after)

    # Package surface
    if surface == "packages":
        return _explain_package_drift(before, after)

    # Scheduler surface
    if surface == "scheduler":
        return _explain_scheduler_drift(before, after)

    # Privilege surface
    if surface == "privilege":
        return _explain_privilege_drift(before, after)

    # Network control surface
    if surface == "network_control":
        return _explain_network_control_drift(before, after)

    # Kernel policy surface
    if surface == "kernel_policy":
        return _explain_kernel_policy_drift(before, after)

    # Hardware surface
    if surface == "hardware":
        return _explain_hardware_drift(before, after)

    return f"Surface '{surface}' changed (unknown surface type)"


def _explain_service_drift(
    service_name: str,
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in a service surface."""
    before_svc = next((s for s in before.services if s.unit_name == service_name), None)
    after_svc = next((s for s in after.services if s.unit_name == service_name), None)

    if not before_svc and after_svc:
        return f"Service '{service_name}' was added to tracking"
    if before_svc and not after_svc:
        return f"Service '{service_name}' was removed from tracking"
    if not before_svc or not after_svc:
        return f"Service '{service_name}' changed"

    changes: list[str] = []

    if before_svc.enabled != after_svc.enabled:
        state = "enabled" if after_svc.enabled else "disabled"
        changes.append(f"was {state}")

    if before_svc.restart_policy != after_svc.restart_policy:
        changes.append(f"restart policy changed to '{after_svc.restart_policy}'")

    if before_svc.memory_max != after_svc.memory_max:
        changes.append(f"memory limit changed to '{after_svc.memory_max}'")

    if before_svc.cpu_quota != after_svc.cpu_quota:
        changes.append(f"CPU quota changed to '{after_svc.cpu_quota}'")

    if before_svc.dropin_count != after_svc.dropin_count:
        delta = after_svc.dropin_count - before_svc.dropin_count
        changes.append(f"drop-in files changed ({delta:+d})")

    if changes:
        return f"Service '{service_name}': " + ", ".join(changes)
    return f"Service '{service_name}' configuration changed"


def _explain_config_drift(
    config_path: str,
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in a config surface."""
    before_cfg = next((c for c in before.configs if c.path == config_path), None)
    after_cfg = next((c for c in after.configs if c.path == config_path), None)

    if not before_cfg and after_cfg:
        return f"Config '{config_path}' was created"
    if before_cfg and not after_cfg:
        return f"Config '{config_path}' was deleted"
    if not before_cfg or not after_cfg:
        return f"Config '{config_path}' changed"

    changes: list[str] = []

    if before_cfg.sha256 != after_cfg.sha256:
        changes.append("contents modified")

    if before_cfg.owner != after_cfg.owner:
        changes.append(f"owner changed to {after_cfg.owner}")

    if before_cfg.mode != after_cfg.mode:
        changes.append(f"mode changed to {after_cfg.mode}")

    if before_cfg.fragment_count != after_cfg.fragment_count:
        delta = after_cfg.fragment_count - before_cfg.fragment_count
        changes.append(f"fragments changed ({delta:+d})")

    if changes:
        return f"Config '{config_path}': " + ", ".join(changes)
    return f"Config '{config_path}' changed"


def _explain_package_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in package surface."""
    before_pkgs = {p.name: p.version for p in before.packages.packages}
    after_pkgs = {p.name: p.version for p in after.packages.packages}

    added = set(after_pkgs.keys()) - set(before_pkgs.keys())
    removed = set(before_pkgs.keys()) - set(after_pkgs.keys())
    common = set(before_pkgs.keys()) & set(after_pkgs.keys())

    upgraded = [p for p in common if before_pkgs[p] != after_pkgs[p]]

    changes: list[str] = []
    if added:
        changes.append(f"{len(added)} package(s) added")
    if removed:
        changes.append(f"{len(removed)} package(s) removed")
    if upgraded:
        changes.append(f"{len(upgraded)} package(s) upgraded")

    if changes:
        return "Packages: " + ", ".join(changes)
    return "Package configuration changed"


def _explain_scheduler_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in scheduler surface."""
    changes: list[str] = []

    if before.scheduler.systemd_timers_hash != after.scheduler.systemd_timers_hash:
        changes.append("systemd timers modified")

    if before.scheduler.cron_entries_hash != after.scheduler.cron_entries_hash:
        changes.append("cron entries modified")

    if before.scheduler.unattended_upgrades != after.scheduler.unattended_upgrades:
        state = "enabled" if after.scheduler.unattended_upgrades else "disabled"
        changes.append(f"unattended upgrades {state}")

    if changes:
        return "Scheduler: " + ", ".join(changes)
    return "Scheduler configuration changed"


def _explain_privilege_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in privilege surface."""
    changes: list[str] = []

    if before.privilege.sudoers_hash != after.privilege.sudoers_hash:
        changes.append("sudoers modified")

    if before.privilege.groups_hash != after.privilege.groups_hash:
        changes.append("groups modified")

    if before.privilege.polkit_rules_hash != after.privilege.polkit_rules_hash:
        changes.append("polkit rules modified")

    if before.privilege.selinux_mode != after.privilege.selinux_mode:
        changes.append(f"SELinux mode changed to {after.privilege.selinux_mode}")

    if before.privilege.apparmor_mode != after.privilege.apparmor_mode:
        changes.append(f"AppArmor mode changed to {after.privilege.apparmor_mode}")

    if changes:
        return "Privilege: " + ", ".join(changes)
    return "Privilege configuration changed"


def _explain_network_control_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in network control surface."""
    changes: list[str] = []

    if before.network_control.network_manager != after.network_control.network_manager:
        changes.append(f"network manager changed to {after.network_control.network_manager}")

    if before.network_control.firewall_frontend != after.network_control.firewall_frontend:
        changes.append(f"firewall changed to {after.network_control.firewall_frontend}")

    if before.network_control.resolver_mode != after.network_control.resolver_mode:
        changes.append(f"resolver changed to {after.network_control.resolver_mode}")

    if changes:
        return "Network control: " + ", ".join(changes)
    return "Network control plane changed"


def _explain_kernel_policy_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in kernel policy surface."""
    changes: list[str] = []

    if before.kernel_policy.kernel_version != after.kernel_policy.kernel_version:
        changes.append(f"kernel upgraded to {after.kernel_policy.kernel_version}")

    if before.kernel_policy.cgroup_mode != after.kernel_policy.cgroup_mode:
        changes.append(f"cgroup mode changed to {after.kernel_policy.cgroup_mode}")

    if before.kernel_policy.swap_enabled != after.kernel_policy.swap_enabled:
        state = "enabled" if after.kernel_policy.swap_enabled else "disabled"
        changes.append(f"swap {state}")

    # Check sysctl changes
    before_sysctls = before.kernel_policy.key_sysctls
    after_sysctls = after.kernel_policy.key_sysctls
    sysctl_changes = [
        k
        for k in set(before_sysctls.keys()) | set(after_sysctls.keys())
        if before_sysctls.get(k) != after_sysctls.get(k)
    ]
    if sysctl_changes:
        changes.append(f"{len(sysctl_changes)} sysctl(s) changed")

    if changes:
        return "Kernel policy: " + ", ".join(changes)
    return "Kernel policy changed"


def _explain_hardware_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Explain drift in hardware surface."""
    changes: list[str] = []

    if before.hardware.disk_layout_hash != after.hardware.disk_layout_hash:
        changes.append("disk layout changed")

    if before.hardware.total_memory_mb != after.hardware.total_memory_mb:
        delta = after.hardware.total_memory_mb - before.hardware.total_memory_mb
        changes.append(f"memory changed ({delta:+d} MB)")

    if before.hardware.cpu_cores != after.hardware.cpu_cores:
        delta = after.hardware.cpu_cores - before.hardware.cpu_cores
        changes.append(f"CPU cores changed ({delta:+d})")

    if before.hardware.virtualization != after.hardware.virtualization:
        changes.append(f"virtualization changed to {after.hardware.virtualization}")

    if set(before.hardware.filesystem_types) != set(after.hardware.filesystem_types):
        changes.append("filesystem types changed")

    if changes:
        return "Hardware: " + ", ".join(changes)
    return "Hardware configuration changed"


def summarize_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> str:
    """Generate a brief summary of all drift.

    Args:
        before: Earlier snapshot.
        after: Later snapshot.

    Returns:
        Human-readable summary string.
    """
    drifted = get_drifted_surfaces(before, after)

    if not drifted:
        return "No configuration drift detected"

    # Categorize drifts
    service_drifts = [s for s in drifted if s.startswith("service:")]
    config_drifts = [s for s in drifted if s.startswith("config:")]
    other_drifts = [s for s in drifted if not s.startswith("service:") and not s.startswith("config:")]

    parts: list[str] = []

    if service_drifts:
        parts.append(f"{len(service_drifts)} service(s)")

    if config_drifts:
        parts.append(f"{len(config_drifts)} config(s)")

    if other_drifts:
        parts.append(", ".join(other_drifts))

    return "Configuration drift: " + ", ".join(parts)


def has_any_drift(
    before: ControlSurfaceSnapshot,
    after: ControlSurfaceSnapshot,
) -> bool:
    """Quick check if any drift occurred.

    Args:
        before: Earlier snapshot.
        after: Later snapshot.

    Returns:
        True if any surface drifted.
    """
    drift = detect_surface_drift(before, after)
    return any(drift.values())
