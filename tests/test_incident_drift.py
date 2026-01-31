from __future__ import annotations

"""Tests for surface drift detection.

Covers all public functions and internal helpers in
elle.daemon.incidents.drift, including every branch of the
_explain_*_drift family.
"""


from elle.daemon.incidents.control_surface import (
    ConfigFileSurface,
    ControlSurfaceSnapshot,
    HardwareIdentitySurface,
    KernelPolicySurface,
    NetworkControlSurface,
    PackageInfo,
    PackageSurface,
    PrivilegeSurface,
    SchedulerSurface,
    SystemdServiceSurface,
)
from elle.daemon.incidents.drift import (
    DriftDetail,
    _explain_config_drift,
    _explain_hardware_drift,
    _explain_kernel_policy_drift,
    _explain_network_control_drift,
    _explain_package_drift,
    _explain_privilege_drift,
    _explain_scheduler_drift,
    _explain_service_drift,
    _explain_surface_drift,
    detect_surface_drift,
    explain_drift,
    get_drifted_surfaces,
    has_any_drift,
    summarize_drift,
)

# ---------------------------------------------------------------------------
# Helpers for building snapshots
# ---------------------------------------------------------------------------


def _make_service(
    unit_name: str = "nginx.service",
    enabled: bool = True,
    restart_policy: str = "always",
    memory_max: str = "1G",
    cpu_quota: str = "50%",
    dropin_count: int = 0,
    effective_hash: str = "aaa111",
) -> SystemdServiceSurface:
    return SystemdServiceSurface(
        unit_name=unit_name,
        enabled=enabled,
        restart_policy=restart_policy,
        memory_max=memory_max,
        cpu_quota=cpu_quota,
        dropin_count=dropin_count,
        effective_hash=effective_hash,
    )


def _make_config(
    path: str = "/etc/ssh/sshd_config",
    sha256: str = "abc123",
    owner: str = "root:root",
    mode: str = "644",
    fragment_count: int = 0,
) -> ConfigFileSurface:
    return ConfigFileSurface(
        path=path,
        sha256=sha256,
        owner=owner,
        mode=mode,
        fragment_count=fragment_count,
    )


def _make_packages(
    packages: tuple[PackageInfo, ...] = (),
    surface_hash_value: str = "",
) -> PackageSurface:
    return PackageSurface(packages=packages, surface_hash_value=surface_hash_value)


def _make_snapshot(
    services: tuple[SystemdServiceSurface, ...] = (),
    configs: tuple[ConfigFileSurface, ...] = (),
    packages: PackageSurface | None = None,
    scheduler: SchedulerSurface | None = None,
    privilege: PrivilegeSurface | None = None,
    network_control: NetworkControlSurface | None = None,
    kernel_policy: KernelPolicySurface | None = None,
    hardware: HardwareIdentitySurface | None = None,
) -> ControlSurfaceSnapshot:
    return ControlSurfaceSnapshot(
        services=services,
        configs=configs,
        packages=packages or PackageSurface(),
        scheduler=scheduler or SchedulerSurface(),
        privilege=privilege or PrivilegeSurface(),
        network_control=network_control or NetworkControlSurface(),
        kernel_policy=kernel_policy or KernelPolicySurface(),
        hardware=hardware or HardwareIdentitySurface(),
    )


# ---------------------------------------------------------------------------
# DriftDetail dataclass
# ---------------------------------------------------------------------------


class TestDriftDetail:
    """Tests for the DriftDetail dataclass."""

    def test_fields(self):
        d = DriftDetail(
            surface="service:nginx",
            hash_before="aaa",
            hash_after="bbb",
            explanation="was disabled",
        )
        assert d.surface == "service:nginx"
        assert d.hash_before == "aaa"
        assert d.hash_after == "bbb"
        assert d.explanation == "was disabled"


# ---------------------------------------------------------------------------
# detect_surface_drift
# ---------------------------------------------------------------------------


class TestDetectSurfaceDrift:
    """Tests for detect_surface_drift."""

    def test_no_drift_identical_snapshots(self):
        snap = _make_snapshot()
        drift = detect_surface_drift(snap, snap)
        # All base surfaces present, none drifted
        assert all(v is False for v in drift.values())

    def test_drift_on_hash_change(self):
        before = _make_snapshot(
            scheduler=SchedulerSurface(
                systemd_timers_hash="hash_a",
                cron_entries_hash="same",
                unattended_upgrades=False,
            ),
        )
        after = _make_snapshot(
            scheduler=SchedulerSurface(
                systemd_timers_hash="hash_b",
                cron_entries_hash="same",
                unattended_upgrades=False,
            ),
        )
        drift = detect_surface_drift(before, after)
        assert drift["scheduler"] is True

    def test_surface_appeared(self):
        """A service that exists only in 'after' is reported as drifted."""
        svc = _make_service(unit_name="new.service", effective_hash="new_hash")
        before = _make_snapshot()
        after = _make_snapshot(services=(svc,))
        drift = detect_surface_drift(before, after)
        assert drift.get("service:new.service") is True

    def test_surface_disappeared(self):
        """A service that exists only in 'before' is reported as drifted."""
        svc = _make_service(unit_name="old.service", effective_hash="old_hash")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot()
        drift = detect_surface_drift(before, after)
        assert drift.get("service:old.service") is True

    def test_config_drift_detected(self):
        cfg_before = _make_config(path="/etc/foo", sha256="aaa")
        cfg_after = _make_config(path="/etc/foo", sha256="bbb")
        before = _make_snapshot(configs=(cfg_before,))
        after = _make_snapshot(configs=(cfg_after,))
        drift = detect_surface_drift(before, after)
        assert drift["config:/etc/foo"] is True

    def test_config_no_drift_when_same(self):
        cfg = _make_config(path="/etc/foo", sha256="same_hash")
        snap = _make_snapshot(configs=(cfg,))
        drift = detect_surface_drift(snap, snap)
        assert drift["config:/etc/foo"] is False

    def test_multiple_surfaces_mixed_drift(self):
        svc_a = _make_service(unit_name="a.service", effective_hash="hash1")
        svc_b = _make_service(unit_name="b.service", effective_hash="hash2")
        svc_b_changed = _make_service(unit_name="b.service", effective_hash="hash_new")
        before = _make_snapshot(services=(svc_a, svc_b))
        after = _make_snapshot(services=(svc_a, svc_b_changed))
        drift = detect_surface_drift(before, after)
        assert drift["service:a.service"] is False
        assert drift["service:b.service"] is True


# ---------------------------------------------------------------------------
# get_drifted_surfaces
# ---------------------------------------------------------------------------


class TestGetDriftedSurfaces:
    """Tests for get_drifted_surfaces."""

    def test_returns_only_drifted_names(self):
        svc = _make_service(unit_name="x.service", effective_hash="h1")
        svc_changed = _make_service(unit_name="x.service", effective_hash="h2")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot(services=(svc_changed,))
        drifted = get_drifted_surfaces(before, after)
        assert "service:x.service" in drifted

    def test_empty_when_no_drift(self):
        snap = _make_snapshot()
        drifted = get_drifted_surfaces(snap, snap)
        assert drifted == []


# ---------------------------------------------------------------------------
# _explain_surface_drift  (dispatch)
# ---------------------------------------------------------------------------


class TestExplainSurfaceDrift:
    """Tests for the _explain_surface_drift dispatcher."""

    def test_dispatch_service(self):
        svc = _make_service(unit_name="foo.service", enabled=True, effective_hash="h1")
        svc2 = _make_service(unit_name="foo.service", enabled=False, effective_hash="h2")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot(services=(svc2,))
        result = _explain_surface_drift("service:foo.service", before, after)
        assert "foo.service" in result

    def test_dispatch_config(self):
        cfg1 = _make_config(path="/etc/test", sha256="aaa")
        cfg2 = _make_config(path="/etc/test", sha256="bbb")
        before = _make_snapshot(configs=(cfg1,))
        after = _make_snapshot(configs=(cfg2,))
        result = _explain_surface_drift("config:/etc/test", before, after)
        assert "/etc/test" in result

    def test_dispatch_packages(self):
        before = _make_snapshot()
        after = _make_snapshot()
        result = _explain_surface_drift("packages", before, after)
        # Packages have same default hash, so "Package configuration changed"
        assert "ackage" in result

    def test_dispatch_scheduler(self):
        snap = _make_snapshot()
        result = _explain_surface_drift("scheduler", snap, snap)
        assert "cheduler" in result

    def test_dispatch_privilege(self):
        snap = _make_snapshot()
        result = _explain_surface_drift("privilege", snap, snap)
        assert "rivilege" in result

    def test_dispatch_network_control(self):
        snap = _make_snapshot()
        result = _explain_surface_drift("network_control", snap, snap)
        assert "etwork" in result

    def test_dispatch_kernel_policy(self):
        snap = _make_snapshot()
        result = _explain_surface_drift("kernel_policy", snap, snap)
        assert "ernel" in result

    def test_dispatch_hardware(self):
        snap = _make_snapshot()
        result = _explain_surface_drift("hardware", snap, snap)
        assert "ardware" in result

    def test_dispatch_unknown_surface(self):
        snap = _make_snapshot()
        result = _explain_surface_drift("alien_surface", snap, snap)
        assert "unknown surface type" in result
        assert "alien_surface" in result


# ---------------------------------------------------------------------------
# _explain_service_drift
# ---------------------------------------------------------------------------


class TestExplainServiceDrift:
    """Tests for _explain_service_drift."""

    def test_service_added(self):
        svc = _make_service(unit_name="new.service")
        before = _make_snapshot()
        after = _make_snapshot(services=(svc,))
        result = _explain_service_drift("new.service", before, after)
        assert "added" in result

    def test_service_removed(self):
        svc = _make_service(unit_name="old.service")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot()
        result = _explain_service_drift("old.service", before, after)
        assert "removed" in result

    def test_service_both_missing(self):
        """Both before and after lack the service."""
        before = _make_snapshot()
        after = _make_snapshot()
        result = _explain_service_drift("ghost.service", before, after)
        assert "ghost.service" in result
        assert "changed" in result

    def test_service_enabled_changed(self):
        svc_before = _make_service(unit_name="s", enabled=True, effective_hash="h1")
        svc_after = _make_service(unit_name="s", enabled=False, effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "disabled" in result

    def test_service_enabled_to_true(self):
        svc_before = _make_service(unit_name="s", enabled=False, effective_hash="h1")
        svc_after = _make_service(unit_name="s", enabled=True, effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "enabled" in result

    def test_service_restart_policy_changed(self):
        svc_before = _make_service(unit_name="s", restart_policy="always", effective_hash="h1")
        svc_after = _make_service(unit_name="s", restart_policy="on-failure", effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "restart policy" in result
        assert "on-failure" in result

    def test_service_memory_max_changed(self):
        svc_before = _make_service(unit_name="s", memory_max="1G", effective_hash="h1")
        svc_after = _make_service(unit_name="s", memory_max="2G", effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "memory limit" in result
        assert "2G" in result

    def test_service_cpu_quota_changed(self):
        svc_before = _make_service(unit_name="s", cpu_quota="50%", effective_hash="h1")
        svc_after = _make_service(unit_name="s", cpu_quota="100%", effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "CPU quota" in result
        assert "100%" in result

    def test_service_dropin_count_changed(self):
        svc_before = _make_service(unit_name="s", dropin_count=1, effective_hash="h1")
        svc_after = _make_service(unit_name="s", dropin_count=3, effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "drop-in" in result
        assert "+2" in result

    def test_service_no_detectable_changes_fallback(self):
        """When both exist but all tracked fields are the same, fallback message."""
        svc = _make_service(unit_name="s", effective_hash="h1")
        # Same fields, only effective_hash differs so snapshot sees drift
        svc2 = _make_service(unit_name="s", effective_hash="h2")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot(services=(svc2,))
        result = _explain_service_drift("s", before, after)
        assert "configuration changed" in result

    def test_service_multiple_changes(self):
        svc_before = _make_service(
            unit_name="s",
            enabled=True,
            restart_policy="always",
            memory_max="1G",
            effective_hash="h1",
        )
        svc_after = _make_service(
            unit_name="s",
            enabled=False,
            restart_policy="no",
            memory_max="4G",
            effective_hash="h2",
        )
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "disabled" in result
        assert "restart policy" in result
        assert "memory limit" in result


# ---------------------------------------------------------------------------
# _explain_config_drift
# ---------------------------------------------------------------------------


class TestExplainConfigDrift:
    """Tests for _explain_config_drift."""

    def test_config_created(self):
        cfg = _make_config(path="/etc/new")
        before = _make_snapshot()
        after = _make_snapshot(configs=(cfg,))
        result = _explain_config_drift("/etc/new", before, after)
        assert "created" in result

    def test_config_deleted(self):
        cfg = _make_config(path="/etc/old")
        before = _make_snapshot(configs=(cfg,))
        after = _make_snapshot()
        result = _explain_config_drift("/etc/old", before, after)
        assert "deleted" in result

    def test_config_both_missing(self):
        before = _make_snapshot()
        after = _make_snapshot()
        result = _explain_config_drift("/etc/ghost", before, after)
        assert "changed" in result

    def test_config_contents_modified(self):
        cfg_before = _make_config(path="/etc/f", sha256="aaa")
        cfg_after = _make_config(path="/etc/f", sha256="bbb")
        before = _make_snapshot(configs=(cfg_before,))
        after = _make_snapshot(configs=(cfg_after,))
        result = _explain_config_drift("/etc/f", before, after)
        assert "contents modified" in result

    def test_config_owner_changed(self):
        cfg_before = _make_config(path="/etc/f", owner="root:root")
        cfg_after = _make_config(path="/etc/f", owner="www:www")
        before = _make_snapshot(configs=(cfg_before,))
        after = _make_snapshot(configs=(cfg_after,))
        result = _explain_config_drift("/etc/f", before, after)
        assert "owner changed" in result
        assert "www:www" in result

    def test_config_mode_changed(self):
        cfg_before = _make_config(path="/etc/f", mode="644")
        cfg_after = _make_config(path="/etc/f", mode="600")
        before = _make_snapshot(configs=(cfg_before,))
        after = _make_snapshot(configs=(cfg_after,))
        result = _explain_config_drift("/etc/f", before, after)
        assert "mode changed" in result
        assert "600" in result

    def test_config_fragment_count_changed(self):
        cfg_before = _make_config(path="/etc/f", fragment_count=2)
        cfg_after = _make_config(path="/etc/f", fragment_count=5)
        before = _make_snapshot(configs=(cfg_before,))
        after = _make_snapshot(configs=(cfg_after,))
        result = _explain_config_drift("/etc/f", before, after)
        assert "fragments changed" in result
        assert "+3" in result

    def test_config_no_detectable_changes_fallback(self):
        cfg = _make_config(path="/etc/f")
        snap = _make_snapshot(configs=(cfg,))
        result = _explain_config_drift("/etc/f", snap, snap)
        assert "changed" in result


# ---------------------------------------------------------------------------
# _explain_package_drift
# ---------------------------------------------------------------------------


class TestExplainPackageDrift:
    """Tests for _explain_package_drift."""

    def test_packages_added(self):
        pkg_after = PackageInfo(name="curl", version="7.88")
        before = _make_snapshot(packages=_make_packages())
        after = _make_snapshot(
            packages=_make_packages(packages=(pkg_after,)),
        )
        result = _explain_package_drift(before, after)
        assert "1 package(s) added" in result

    def test_packages_removed(self):
        pkg_before = PackageInfo(name="curl", version="7.88")
        before = _make_snapshot(
            packages=_make_packages(packages=(pkg_before,)),
        )
        after = _make_snapshot(packages=_make_packages())
        result = _explain_package_drift(before, after)
        assert "1 package(s) removed" in result

    def test_packages_upgraded(self):
        pkg_before = PackageInfo(name="curl", version="7.88")
        pkg_after = PackageInfo(name="curl", version="8.0")
        before = _make_snapshot(
            packages=_make_packages(packages=(pkg_before,)),
        )
        after = _make_snapshot(
            packages=_make_packages(packages=(pkg_after,)),
        )
        result = _explain_package_drift(before, after)
        assert "1 package(s) upgraded" in result

    def test_packages_no_detectable_changes(self):
        pkg = PackageInfo(name="curl", version="7.88")
        snap = _make_snapshot(packages=_make_packages(packages=(pkg,)))
        result = _explain_package_drift(snap, snap)
        assert "Package configuration changed" in result

    def test_packages_mixed_changes(self):
        pkg_a = PackageInfo(name="a", version="1")
        pkg_b = PackageInfo(name="b", version="1")
        pkg_b_up = PackageInfo(name="b", version="2")
        pkg_c = PackageInfo(name="c", version="1")

        before = _make_snapshot(
            packages=_make_packages(packages=(pkg_a, pkg_b)),
        )
        after = _make_snapshot(
            packages=_make_packages(packages=(pkg_b_up, pkg_c)),
        )
        result = _explain_package_drift(before, after)
        assert "added" in result
        assert "removed" in result
        assert "upgraded" in result


# ---------------------------------------------------------------------------
# _explain_scheduler_drift
# ---------------------------------------------------------------------------


class TestExplainSchedulerDrift:
    """Tests for _explain_scheduler_drift."""

    def test_timers_changed(self):
        before = _make_snapshot(
            scheduler=SchedulerSurface(systemd_timers_hash="t1"),
        )
        after = _make_snapshot(
            scheduler=SchedulerSurface(systemd_timers_hash="t2"),
        )
        result = _explain_scheduler_drift(before, after)
        assert "systemd timers modified" in result

    def test_cron_changed(self):
        before = _make_snapshot(
            scheduler=SchedulerSurface(cron_entries_hash="c1"),
        )
        after = _make_snapshot(
            scheduler=SchedulerSurface(cron_entries_hash="c2"),
        )
        result = _explain_scheduler_drift(before, after)
        assert "cron entries modified" in result

    def test_unattended_upgrades_enabled(self):
        before = _make_snapshot(
            scheduler=SchedulerSurface(unattended_upgrades=False),
        )
        after = _make_snapshot(
            scheduler=SchedulerSurface(unattended_upgrades=True),
        )
        result = _explain_scheduler_drift(before, after)
        assert "unattended upgrades enabled" in result

    def test_unattended_upgrades_disabled(self):
        before = _make_snapshot(
            scheduler=SchedulerSurface(unattended_upgrades=True),
        )
        after = _make_snapshot(
            scheduler=SchedulerSurface(unattended_upgrades=False),
        )
        result = _explain_scheduler_drift(before, after)
        assert "unattended upgrades disabled" in result

    def test_scheduler_no_detectable_changes(self):
        snap = _make_snapshot()
        result = _explain_scheduler_drift(snap, snap)
        assert "Scheduler configuration changed" in result


# ---------------------------------------------------------------------------
# _explain_privilege_drift
# ---------------------------------------------------------------------------


class TestExplainPrivilegeDrift:
    """Tests for _explain_privilege_drift."""

    def test_sudoers_changed(self):
        before = _make_snapshot(privilege=PrivilegeSurface(sudoers_hash="a"))
        after = _make_snapshot(privilege=PrivilegeSurface(sudoers_hash="b"))
        result = _explain_privilege_drift(before, after)
        assert "sudoers modified" in result

    def test_groups_changed(self):
        before = _make_snapshot(privilege=PrivilegeSurface(groups_hash="a"))
        after = _make_snapshot(privilege=PrivilegeSurface(groups_hash="b"))
        result = _explain_privilege_drift(before, after)
        assert "groups modified" in result

    def test_polkit_rules_changed(self):
        before = _make_snapshot(privilege=PrivilegeSurface(polkit_rules_hash="a"))
        after = _make_snapshot(privilege=PrivilegeSurface(polkit_rules_hash="b"))
        result = _explain_privilege_drift(before, after)
        assert "polkit rules modified" in result

    def test_selinux_mode_changed(self):
        before = _make_snapshot(privilege=PrivilegeSurface(selinux_mode="disabled"))
        after = _make_snapshot(privilege=PrivilegeSurface(selinux_mode="enforcing"))
        result = _explain_privilege_drift(before, after)
        assert "SELinux mode changed" in result
        assert "enforcing" in result

    def test_apparmor_mode_changed(self):
        before = _make_snapshot(privilege=PrivilegeSurface(apparmor_mode="unknown"))
        after = _make_snapshot(privilege=PrivilegeSurface(apparmor_mode="enabled"))
        result = _explain_privilege_drift(before, after)
        assert "AppArmor mode changed" in result
        assert "enabled" in result

    def test_privilege_no_detectable_changes(self):
        snap = _make_snapshot()
        result = _explain_privilege_drift(snap, snap)
        assert "Privilege configuration changed" in result

    def test_privilege_multiple_changes(self):
        before = _make_snapshot(
            privilege=PrivilegeSurface(
                sudoers_hash="a",
                groups_hash="a",
                selinux_mode="disabled",
            ),
        )
        after = _make_snapshot(
            privilege=PrivilegeSurface(
                sudoers_hash="b",
                groups_hash="b",
                selinux_mode="enforcing",
            ),
        )
        result = _explain_privilege_drift(before, after)
        assert "sudoers modified" in result
        assert "groups modified" in result
        assert "SELinux mode changed" in result


# ---------------------------------------------------------------------------
# _explain_network_control_drift
# ---------------------------------------------------------------------------


class TestExplainNetworkControlDrift:
    """Tests for _explain_network_control_drift."""

    def test_network_manager_changed(self):
        before = _make_snapshot(
            network_control=NetworkControlSurface(network_manager="NetworkManager"),
        )
        after = _make_snapshot(
            network_control=NetworkControlSurface(network_manager="systemd-networkd"),
        )
        result = _explain_network_control_drift(before, after)
        assert "network manager changed" in result
        assert "systemd-networkd" in result

    def test_firewall_changed(self):
        before = _make_snapshot(
            network_control=NetworkControlSurface(firewall_frontend="ufw"),
        )
        after = _make_snapshot(
            network_control=NetworkControlSurface(firewall_frontend="nftables"),
        )
        result = _explain_network_control_drift(before, after)
        assert "firewall changed" in result
        assert "nftables" in result

    def test_resolver_changed(self):
        before = _make_snapshot(
            network_control=NetworkControlSurface(resolver_mode="static"),
        )
        after = _make_snapshot(
            network_control=NetworkControlSurface(resolver_mode="systemd-resolved"),
        )
        result = _explain_network_control_drift(before, after)
        assert "resolver changed" in result
        assert "systemd-resolved" in result

    def test_network_control_no_detectable_changes(self):
        snap = _make_snapshot()
        result = _explain_network_control_drift(snap, snap)
        assert "Network control plane changed" in result


# ---------------------------------------------------------------------------
# _explain_kernel_policy_drift
# ---------------------------------------------------------------------------


class TestExplainKernelPolicyDrift:
    """Tests for _explain_kernel_policy_drift."""

    def test_kernel_version_changed(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(kernel_version="5.15.0"),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(kernel_version="6.2.0"),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "kernel upgraded" in result
        assert "6.2.0" in result

    def test_cgroup_mode_changed(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(cgroup_mode="v1"),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(cgroup_mode="v2"),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "cgroup mode changed" in result
        assert "v2" in result

    def test_swap_enabled(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(swap_enabled=False),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(swap_enabled=True),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "swap enabled" in result

    def test_swap_disabled(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(swap_enabled=True),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(swap_enabled=False),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "swap disabled" in result

    def test_sysctl_changes(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(
                key_sysctls={"vm.swappiness": "60", "net.ipv4.ip_forward": "0"},
            ),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(
                key_sysctls={"vm.swappiness": "10", "net.ipv4.ip_forward": "1"},
            ),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "2 sysctl(s) changed" in result

    def test_sysctl_added(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(key_sysctls={}),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(key_sysctls={"vm.swappiness": "10"}),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "1 sysctl(s) changed" in result

    def test_sysctl_removed(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(
                key_sysctls={"vm.swappiness": "60"},
            ),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(key_sysctls={}),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "1 sysctl(s) changed" in result

    def test_kernel_policy_no_detectable_changes(self):
        snap = _make_snapshot()
        result = _explain_kernel_policy_drift(snap, snap)
        assert "Kernel policy changed" in result

    def test_kernel_policy_multiple_changes(self):
        before = _make_snapshot(
            kernel_policy=KernelPolicySurface(
                kernel_version="5.15.0",
                cgroup_mode="v1",
                swap_enabled=False,
                key_sysctls={"vm.swappiness": "60"},
            ),
        )
        after = _make_snapshot(
            kernel_policy=KernelPolicySurface(
                kernel_version="6.2.0",
                cgroup_mode="v2",
                swap_enabled=True,
                key_sysctls={"vm.swappiness": "10"},
            ),
        )
        result = _explain_kernel_policy_drift(before, after)
        assert "kernel upgraded" in result
        assert "cgroup mode changed" in result
        assert "swap enabled" in result
        assert "sysctl(s) changed" in result


# ---------------------------------------------------------------------------
# _explain_hardware_drift
# ---------------------------------------------------------------------------


class TestExplainHardwareDrift:
    """Tests for _explain_hardware_drift."""

    def test_disk_layout_changed(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(disk_layout_hash="d1"),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(disk_layout_hash="d2"),
        )
        result = _explain_hardware_drift(before, after)
        assert "disk layout changed" in result

    def test_memory_changed(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(total_memory_mb=8192),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(total_memory_mb=16384),
        )
        result = _explain_hardware_drift(before, after)
        assert "memory changed" in result
        assert "+8192" in result

    def test_memory_decreased(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(total_memory_mb=16384),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(total_memory_mb=8192),
        )
        result = _explain_hardware_drift(before, after)
        assert "memory changed" in result
        assert "-8192" in result

    def test_cpu_cores_changed(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(cpu_cores=4),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(cpu_cores=8),
        )
        result = _explain_hardware_drift(before, after)
        assert "CPU cores changed" in result
        assert "+4" in result

    def test_virtualization_changed(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(virtualization="none"),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(virtualization="kvm"),
        )
        result = _explain_hardware_drift(before, after)
        assert "virtualization changed" in result
        assert "kvm" in result

    def test_filesystem_types_changed(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(filesystem_types=("ext4",)),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(filesystem_types=("ext4", "btrfs")),
        )
        result = _explain_hardware_drift(before, after)
        assert "filesystem types changed" in result

    def test_hardware_no_detectable_changes(self):
        snap = _make_snapshot()
        result = _explain_hardware_drift(snap, snap)
        assert "Hardware configuration changed" in result

    def test_hardware_multiple_changes(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(
                disk_layout_hash="d1",
                total_memory_mb=8192,
                cpu_cores=4,
            ),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(
                disk_layout_hash="d2",
                total_memory_mb=16384,
                cpu_cores=8,
            ),
        )
        result = _explain_hardware_drift(before, after)
        assert "disk layout changed" in result
        assert "memory changed" in result
        assert "CPU cores changed" in result


# ---------------------------------------------------------------------------
# explain_drift
# ---------------------------------------------------------------------------


class TestExplainDrift:
    """Tests for explain_drift."""

    def test_explain_with_explicit_surfaces(self):
        svc_before = _make_service(unit_name="s", enabled=True, effective_hash="h1")
        svc_after = _make_service(unit_name="s", enabled=False, effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        details = explain_drift(before, after, drifted_surfaces=["service:s"])
        assert len(details) == 1
        assert isinstance(details[0], DriftDetail)
        assert details[0].surface == "service:s"
        assert "disabled" in details[0].explanation

    def test_explain_auto_detects_when_none(self):
        svc_before = _make_service(unit_name="s", enabled=True, effective_hash="h1")
        svc_after = _make_service(unit_name="s", enabled=False, effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        details = explain_drift(before, after)
        drifted_names = [d.surface for d in details]
        assert "service:s" in drifted_names

    def test_explain_empty_when_no_drift(self):
        snap = _make_snapshot()
        details = explain_drift(snap, snap)
        assert details == []

    def test_explain_populates_hashes(self):
        svc_before = _make_service(unit_name="s", effective_hash="hash_before")
        svc_after = _make_service(unit_name="s", effective_hash="hash_after")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        details = explain_drift(before, after, drifted_surfaces=["service:s"])
        assert details[0].hash_before == "hash_before"
        assert details[0].hash_after == "hash_after"

    def test_explain_multiple_surfaces(self):
        svc_before = _make_service(unit_name="s", enabled=True, effective_hash="h1")
        svc_after = _make_service(unit_name="s", enabled=False, effective_hash="h2")
        cfg_before = _make_config(path="/etc/f", sha256="aaa")
        cfg_after = _make_config(path="/etc/f", sha256="bbb")
        before = _make_snapshot(services=(svc_before,), configs=(cfg_before,))
        after = _make_snapshot(services=(svc_after,), configs=(cfg_after,))
        details = explain_drift(before, after)
        surfaces = {d.surface for d in details}
        assert "service:s" in surfaces
        assert "config:/etc/f" in surfaces

    def test_explain_surface_missing_from_before(self):
        """A surface in drifted_surfaces that has no hash in before gets empty string."""
        svc = _make_service(unit_name="new.service", effective_hash="h1")
        before = _make_snapshot()
        after = _make_snapshot(services=(svc,))
        details = explain_drift(before, after, drifted_surfaces=["service:new.service"])
        assert details[0].hash_before == ""
        assert details[0].hash_after != ""


# ---------------------------------------------------------------------------
# summarize_drift
# ---------------------------------------------------------------------------


class TestSummarizeDrift:
    """Tests for summarize_drift."""

    def test_no_drift(self):
        snap = _make_snapshot()
        result = summarize_drift(snap, snap)
        assert result == "No configuration drift detected"

    def test_service_drift_counted(self):
        svc_before = _make_service(unit_name="a", effective_hash="h1")
        svc_after = _make_service(unit_name="a", effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = summarize_drift(before, after)
        assert "1 service(s)" in result

    def test_config_drift_counted(self):
        cfg_before = _make_config(path="/etc/f", sha256="aaa")
        cfg_after = _make_config(path="/etc/f", sha256="bbb")
        before = _make_snapshot(configs=(cfg_before,))
        after = _make_snapshot(configs=(cfg_after,))
        result = summarize_drift(before, after)
        assert "1 config(s)" in result

    def test_other_drift_named(self):
        before = _make_snapshot(
            scheduler=SchedulerSurface(systemd_timers_hash="t1"),
        )
        after = _make_snapshot(
            scheduler=SchedulerSurface(systemd_timers_hash="t2"),
        )
        result = summarize_drift(before, after)
        assert "scheduler" in result

    def test_mixed_drift_summary(self):
        svc_before = _make_service(unit_name="s", effective_hash="h1")
        svc_after = _make_service(unit_name="s", effective_hash="h2")
        cfg_before = _make_config(path="/etc/f", sha256="aaa")
        cfg_after = _make_config(path="/etc/f", sha256="bbb")
        before = _make_snapshot(
            services=(svc_before,),
            configs=(cfg_before,),
            scheduler=SchedulerSurface(systemd_timers_hash="t1"),
        )
        after = _make_snapshot(
            services=(svc_after,),
            configs=(cfg_after,),
            scheduler=SchedulerSurface(systemd_timers_hash="t2"),
        )
        result = summarize_drift(before, after)
        assert "Configuration drift:" in result
        assert "1 service(s)" in result
        assert "1 config(s)" in result
        assert "scheduler" in result

    def test_summary_multiple_services(self):
        svc_a = _make_service(unit_name="a", effective_hash="h1")
        svc_b = _make_service(unit_name="b", effective_hash="h2")
        svc_a2 = _make_service(unit_name="a", effective_hash="h3")
        svc_b2 = _make_service(unit_name="b", effective_hash="h4")
        before = _make_snapshot(services=(svc_a, svc_b))
        after = _make_snapshot(services=(svc_a2, svc_b2))
        result = summarize_drift(before, after)
        assert "2 service(s)" in result


# ---------------------------------------------------------------------------
# has_any_drift
# ---------------------------------------------------------------------------


class TestHasAnyDrift:
    """Tests for has_any_drift."""

    def test_true_when_drift_exists(self):
        svc = _make_service(unit_name="s", effective_hash="h1")
        svc2 = _make_service(unit_name="s", effective_hash="h2")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot(services=(svc2,))
        assert has_any_drift(before, after) is True

    def test_false_when_no_drift(self):
        snap = _make_snapshot()
        assert has_any_drift(snap, snap) is False

    def test_true_on_surface_appearance(self):
        svc = _make_service(unit_name="new.service")
        before = _make_snapshot()
        after = _make_snapshot(services=(svc,))
        assert has_any_drift(before, after) is True

    def test_true_on_surface_disappearance(self):
        svc = _make_service(unit_name="old.service")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot()
        assert has_any_drift(before, after) is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and integration-level tests."""

    def test_filesystem_types_order_does_not_matter(self):
        """Filesystem sets should compare regardless of tuple ordering."""
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(filesystem_types=("ext4", "btrfs")),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(filesystem_types=("btrfs", "ext4")),
        )
        # Same set, different tuple ordering: _explain_hardware_drift should NOT report
        result = _explain_hardware_drift(before, after)
        assert "filesystem types changed" not in result

    def test_filesystem_types_actually_different(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(filesystem_types=("ext4",)),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(filesystem_types=("xfs",)),
        )
        result = _explain_hardware_drift(before, after)
        assert "filesystem types changed" in result

    def test_empty_snapshots_no_drift(self):
        """Two default-constructed snapshots have no drift."""
        a = _make_snapshot()
        b = _make_snapshot()
        assert has_any_drift(a, b) is False
        assert get_drifted_surfaces(a, b) == []
        assert summarize_drift(a, b) == "No configuration drift detected"
        assert explain_drift(a, b) == []

    def test_dropin_count_decrease(self):
        svc_before = _make_service(unit_name="s", dropin_count=5, effective_hash="h1")
        svc_after = _make_service(unit_name="s", dropin_count=2, effective_hash="h2")
        before = _make_snapshot(services=(svc_before,))
        after = _make_snapshot(services=(svc_after,))
        result = _explain_service_drift("s", before, after)
        assert "drop-in" in result
        assert "-3" in result

    def test_fragment_count_decrease(self):
        cfg_before = _make_config(path="/etc/f", fragment_count=10)
        cfg_after = _make_config(path="/etc/f", fragment_count=3)
        before = _make_snapshot(configs=(cfg_before,))
        after = _make_snapshot(configs=(cfg_after,))
        result = _explain_config_drift("/etc/f", before, after)
        assert "fragments changed" in result
        assert "-7" in result

    def test_cpu_cores_decrease(self):
        before = _make_snapshot(
            hardware=HardwareIdentitySurface(cpu_cores=8),
        )
        after = _make_snapshot(
            hardware=HardwareIdentitySurface(cpu_cores=4),
        )
        result = _explain_hardware_drift(before, after)
        assert "CPU cores changed" in result
        assert "-4" in result

    def test_explain_drift_hash_for_missing_after(self):
        """When a surface disappears, its after-hash should be empty string."""
        svc = _make_service(unit_name="gone.service", effective_hash="was_here")
        before = _make_snapshot(services=(svc,))
        after = _make_snapshot()
        details = explain_drift(before, after, drifted_surfaces=["service:gone.service"])
        assert details[0].hash_after == ""
        assert details[0].hash_before != ""

    def test_sysctl_no_changes_same_dict(self):
        """Identical sysctl dicts should not generate sysctl change message."""
        kp = KernelPolicySurface(key_sysctls={"vm.swappiness": "60"})
        snap = _make_snapshot(kernel_policy=kp)
        result = _explain_kernel_policy_drift(snap, snap)
        assert "sysctl" not in result
