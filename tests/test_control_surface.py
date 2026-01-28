"""Tests for control surface snapshot collection and models."""

from datetime import datetime, timezone
import json

import pytest

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
    collect_control_surface,
    KNOWN_CONFIG_ROOTS,
    KEY_SYSCTLS,
)


class TestControlSurfaceModels:
    """Tests for control surface model classes."""

    def test_systemd_service_surface(self) -> None:
        """SystemdServiceSurface should capture service config."""
        svc = SystemdServiceSurface(
            unit_name="nginx.service",
            enabled=True,
            active_state="active",
            restart_policy="always",
            effective_hash="abc123",
        )
        assert svc.unit_name == "nginx.service"
        assert svc.enabled is True
        assert svc.surface_hash() == "abc123"

    def test_systemd_service_surface_hash_fallback(self) -> None:
        """SystemdServiceSurface should compute hash if not provided."""
        svc = SystemdServiceSurface(
            unit_name="nginx.service",
            enabled=True,
            restart_policy="always",
        )
        # Should generate a hash
        h = svc.surface_hash()
        assert len(h) == 16

    def test_config_file_surface(self) -> None:
        """ConfigFileSurface should capture config state."""
        cfg = ConfigFileSurface(
            path="/etc/nginx/nginx.conf",
            owner="0:0",
            mode="644",
            sha256="deadbeef" * 8,
        )
        assert cfg.path == "/etc/nginx/nginx.conf"
        assert cfg.surface_hash() == "deadbeef" * 2  # First 16 chars

    def test_package_surface(self) -> None:
        """PackageSurface should capture package versions."""
        pkgs = (
            PackageInfo(name="nginx", version="1.18.0"),
            PackageInfo(name="docker.io", version="20.10.0"),
        )
        surface = PackageSurface(packages=pkgs)
        h = surface.surface_hash()
        assert len(h) == 16

    def test_scheduler_surface(self) -> None:
        """SchedulerSurface should capture scheduler config."""
        sched = SchedulerSurface(
            systemd_timers_hash="timer123",
            cron_entries_hash="cron456",
            unattended_upgrades=True,
        )
        assert sched.unattended_upgrades is True
        h = sched.surface_hash()
        assert len(h) == 16

    def test_privilege_surface(self) -> None:
        """PrivilegeSurface should capture security config."""
        priv = PrivilegeSurface(
            sudoers_hash="sudo123",
            groups_hash="groups456",
            selinux_mode="enforcing",
            apparmor_mode="enabled",
        )
        assert priv.selinux_mode == "enforcing"
        h = priv.surface_hash()
        assert len(h) == 16

    def test_network_control_surface(self) -> None:
        """NetworkControlSurface should capture network config."""
        net = NetworkControlSurface(
            network_manager="NetworkManager",
            firewall_frontend="ufw",
            resolver_mode="systemd-resolved",
        )
        assert net.network_manager == "NetworkManager"
        h = net.surface_hash()
        assert len(h) == 16

    def test_kernel_policy_surface(self) -> None:
        """KernelPolicySurface should capture kernel config."""
        kernel = KernelPolicySurface(
            kernel_version="5.15.0-generic",
            cgroup_mode="v2",
            key_sysctls={"vm.swappiness": "60"},
            swap_enabled=True,
        )
        assert kernel.kernel_version == "5.15.0-generic"
        assert kernel.cgroup_mode == "v2"
        h = kernel.surface_hash()
        assert len(h) == 16

    def test_hardware_identity_surface(self) -> None:
        """HardwareIdentitySurface should capture hardware config."""
        hw = HardwareIdentitySurface(
            disk_layout_hash="disk123",
            filesystem_types=("ext4", "xfs"),
            total_memory_mb=16000,
            cpu_cores=8,
            virtualization="kvm",
        )
        assert hw.total_memory_mb == 16000
        assert hw.cpu_cores == 8
        h = hw.surface_hash()
        assert len(h) == 16


class TestControlSurfaceSnapshot:
    """Tests for the complete ControlSurfaceSnapshot model."""

    def test_control_surface_construction(self) -> None:
        """ControlSurfaceSnapshot should construct from surfaces."""
        snap = ControlSurfaceSnapshot(
            packages=PackageSurface(packages=()),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(),
            hardware=HardwareIdentitySurface(),
        )
        assert snap.packages is not None
        assert snap.collected_at is not None

    def test_control_surface_with_services(self) -> None:
        """ControlSurfaceSnapshot should include service surfaces."""
        services = (
            SystemdServiceSurface(unit_name="nginx.service", enabled=True),
            SystemdServiceSurface(unit_name="docker.service", enabled=True),
        )
        snap = ControlSurfaceSnapshot(
            services=services,
            packages=PackageSurface(packages=()),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(),
            hardware=HardwareIdentitySurface(),
        )
        assert len(snap.services) == 2

    def test_surface_hashes(self) -> None:
        """surface_hashes should return dict of all surface hashes."""
        services = (SystemdServiceSurface(unit_name="nginx.service", enabled=True),)
        configs = (ConfigFileSurface(path="/etc/nginx", sha256="abc" * 22),)
        snap = ControlSurfaceSnapshot(
            services=services,
            configs=configs,
            packages=PackageSurface(packages=()),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(),
            hardware=HardwareIdentitySurface(),
        )
        hashes = snap.surface_hashes()
        assert "packages" in hashes
        assert "scheduler" in hashes
        assert "service:nginx.service" in hashes
        assert "config:/etc/nginx" in hashes

    def test_control_surface_serialization(self) -> None:
        """ControlSurfaceSnapshot should serialize to JSON."""
        snap = ControlSurfaceSnapshot(
            packages=PackageSurface(packages=()),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(kernel_version="5.15.0"),
            hardware=HardwareIdentitySurface(cpu_cores=4),
        )
        data = snap.model_dump()
        json_str = json.dumps(data, default=str)
        assert "5.15.0" in json_str

    def test_control_surface_is_frozen(self) -> None:
        """ControlSurfaceSnapshot should be immutable."""
        snap = ControlSurfaceSnapshot(
            packages=PackageSurface(packages=()),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(),
            hardware=HardwareIdentitySurface(),
        )
        with pytest.raises(Exception):
            snap.hardware = HardwareIdentitySurface()  # type: ignore[misc]


class TestControlSurfaceCollection:
    """Tests for control surface collection."""

    def test_collect_returns_snapshot(self) -> None:
        """collect_control_surface should return a ControlSurfaceSnapshot."""
        snap = collect_control_surface()
        assert isinstance(snap, ControlSurfaceSnapshot)

    def test_collect_has_hardware(self) -> None:
        """Collected snapshot should have hardware surface."""
        snap = collect_control_surface()
        assert snap.hardware is not None
        # CPU cores should be detected
        assert snap.hardware.cpu_cores >= 0

    def test_collect_has_kernel_policy(self) -> None:
        """Collected snapshot should have kernel policy."""
        snap = collect_control_surface()
        assert snap.kernel_policy is not None
        # Kernel version should be detected
        assert len(snap.kernel_policy.kernel_version) > 0

    def test_collect_with_services(self) -> None:
        """Collecting with services should include service surfaces."""
        snap = collect_control_surface(services=["nonexistent.service"])
        # Should not crash
        assert isinstance(snap.services, tuple)

    def test_collect_with_config_paths(self) -> None:
        """Collecting with config paths should include config surfaces."""
        snap = collect_control_surface(config_paths=["/etc/hosts"])
        # Should find /etc/hosts or other configs
        assert isinstance(snap.configs, tuple)

    def test_surface_hashes_consistent(self) -> None:
        """surface_hashes should be consistent across calls."""
        snap1 = collect_control_surface()
        snap2 = collect_control_surface()
        # Hardware should be the same
        assert snap1.hardware.surface_hash() == snap2.hardware.surface_hash()


class TestKnownConstants:
    """Tests for module constants."""

    def test_known_config_roots_exists(self) -> None:
        """KNOWN_CONFIG_ROOTS should be defined."""
        assert len(KNOWN_CONFIG_ROOTS) > 0
        assert "/etc/ssh" in KNOWN_CONFIG_ROOTS

    def test_key_sysctls_exists(self) -> None:
        """KEY_SYSCTLS should be defined."""
        assert len(KEY_SYSCTLS) > 0
        assert "vm.swappiness" in KEY_SYSCTLS
