"""Tests for control surface snapshot collection and models."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elle.daemon.incidents.control_surface import (
    KEY_SYSCTLS,
    KNOWN_CONFIG_ROOTS,
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
    _collect_config_surface,
    _collect_hardware_identity_surface,
    _collect_kernel_policy_surface,
    _collect_network_control_surface,
    _collect_package_surface,
    _collect_privilege_surface,
    _collect_scheduler_surface,
    _collect_service_surface,
    _hash_directory,
    _hash_file,
    collect_control_surface,
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


# ---------------------------------------------------------------------------
# Extended coverage: _hash_file and _hash_directory
# ---------------------------------------------------------------------------


class TestHashFile:
    def test_hash_existing_file(self, tmp_path: Path) -> None:
        """_hash_file should return sha256 for existing file."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = _hash_file(f)
        assert len(result) == 64  # SHA256 hex digest

    def test_hash_nonexistent_file(self, tmp_path: Path) -> None:
        """_hash_file should return empty string for missing file."""
        result = _hash_file(tmp_path / "missing.txt")
        assert result == ""


class TestHashDirectory:
    def test_hash_existing_dir(self, tmp_path: Path) -> None:
        """_hash_directory should return sha256 for existing directory."""
        (tmp_path / "a.txt").write_text("aaa")
        (tmp_path / "b.txt").write_text("bbb")
        result = _hash_directory(tmp_path)
        assert len(result) == 64

    def test_hash_nonexistent_dir(self, tmp_path: Path) -> None:
        """_hash_directory should return empty string for missing dir."""
        result = _hash_directory(tmp_path / "missing")
        assert result == ""

    def test_hash_file_as_dir(self, tmp_path: Path) -> None:
        """_hash_directory should return empty string for a file path."""
        f = tmp_path / "afile.txt"
        f.write_text("data")
        result = _hash_directory(f)
        assert result == ""

    def test_hash_empty_dir(self, tmp_path: Path) -> None:
        """_hash_directory should hash empty directory."""
        d = tmp_path / "emptydir"
        d.mkdir()
        result = _hash_directory(d)
        assert len(result) == 64


# ---------------------------------------------------------------------------
# Extended coverage: _collect_service_surface
# ---------------------------------------------------------------------------


class TestCollectServiceSurface:
    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_service_success(self, mock_run: MagicMock) -> None:
        """_collect_service_surface should parse systemctl show output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="UnitFileState=enabled\nActiveState=active\nRestart=always\nUser=www\nGroup=www\nCPUQuota=50%\nMemoryMax=1G",
        )
        result = _collect_service_surface("nginx.service")
        assert result is not None
        assert result.unit_name == "nginx.service"
        assert result.enabled is True
        assert result.active_state == "active"

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_service_not_found(self, mock_run: MagicMock) -> None:
        """_collect_service_surface should return None on failure."""
        mock_run.return_value = MagicMock(returncode=4, stdout="")
        result = _collect_service_surface("nonexistent.service")
        assert result is None

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_service_exception(self, mock_run: MagicMock) -> None:
        """_collect_service_surface should return None on exception."""
        mock_run.side_effect = Exception("timeout")
        result = _collect_service_surface("nginx.service")
        assert result is None

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_service_with_effective_hash(self, mock_run: MagicMock) -> None:
        """_collect_service_surface should compute effective hash from systemctl cat."""
        # First call: systemctl show
        show_result = MagicMock(
            returncode=0,
            stdout="UnitFileState=enabled\nActiveState=active",
        )
        # Second call: systemctl cat
        cat_result = MagicMock(
            returncode=0,
            stdout="[Unit]\nDescription=Test",
        )
        mock_run.side_effect = [show_result, cat_result]
        result = _collect_service_surface("test.service")
        assert result is not None
        assert result.effective_hash != ""


# ---------------------------------------------------------------------------
# Extended coverage: _collect_config_surface
# ---------------------------------------------------------------------------


class TestCollectConfigSurface:
    def test_collect_config_file(self, tmp_path: Path) -> None:
        """_collect_config_surface should capture file state."""
        f = tmp_path / "test.conf"
        f.write_text("config data")
        result = _collect_config_surface(str(f))
        assert result is not None
        assert result.path == str(f)
        assert result.sha256 != ""

    def test_collect_config_directory(self, tmp_path: Path) -> None:
        """_collect_config_surface should capture directory state."""
        d = tmp_path / "conf.d"
        d.mkdir()
        (d / "01-main.conf").write_text("main config")
        (d / "02-extra.conf").write_text("extra config")
        result = _collect_config_surface(str(d))
        assert result is not None
        assert result.fragment_count >= 0

    def test_collect_config_nonexistent(self) -> None:
        """_collect_config_surface should return None for missing path."""
        result = _collect_config_surface("/nonexistent/path/file.conf")
        assert result is None

    def test_collect_config_with_d_suffix(self, tmp_path: Path) -> None:
        """_collect_config_surface should handle *.d directories."""
        d = tmp_path / "sudoers.d"
        d.mkdir()
        (d / "custom").write_text("custom rule")
        result = _collect_config_surface(str(d))
        assert result is not None
        assert result.fragment_count >= 0


# ---------------------------------------------------------------------------
# Extended coverage: _collect_package_surface
# ---------------------------------------------------------------------------


class TestCollectPackageSurface:
    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_packages_default(self, mock_run: MagicMock) -> None:
        """_collect_package_surface should use default packages."""
        # dpkg-query returns version for systemd, fails for others
        def run_side_effect(cmd, **kwargs):
            if cmd[0] == "dpkg-query":
                pkg_name = cmd[-1]
                if pkg_name == "systemd":
                    return MagicMock(returncode=0, stdout="249.11-0ubuntu3")
                return MagicMock(returncode=1, stdout="")
            elif cmd[0] == "apt-mark":
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=1, stdout="")

        mock_run.side_effect = run_side_effect
        result = _collect_package_surface()
        assert isinstance(result, PackageSurface)
        assert result.surface_hash_value != ""

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_packages_custom_list(self, mock_run: MagicMock) -> None:
        """_collect_package_surface should accept custom packages."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1.0.0")
        result = _collect_package_surface(["mypkg"])
        assert isinstance(result, PackageSurface)

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_packages_exception(self, mock_run: MagicMock) -> None:
        """_collect_package_surface should handle exceptions."""
        mock_run.side_effect = Exception("fail")
        result = _collect_package_surface()
        assert isinstance(result, PackageSurface)


# ---------------------------------------------------------------------------
# Extended coverage: _collect_scheduler_surface
# ---------------------------------------------------------------------------


class TestCollectSchedulerSurface:
    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_scheduler(self, mock_run: MagicMock) -> None:
        """_collect_scheduler_surface should collect timer and cron info."""
        mock_run.return_value = MagicMock(returncode=0, stdout="NEXT LEFT LAST PASSED UNIT ACTIVATES")
        result = _collect_scheduler_surface()
        assert isinstance(result, SchedulerSurface)

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_scheduler_exception(self, mock_run: MagicMock) -> None:
        """_collect_scheduler_surface should handle exceptions."""
        mock_run.side_effect = Exception("fail")
        result = _collect_scheduler_surface()
        assert isinstance(result, SchedulerSurface)
        assert result.systemd_timers_hash == ""


# ---------------------------------------------------------------------------
# Extended coverage: _collect_privilege_surface
# ---------------------------------------------------------------------------


class TestCollectPrivilegeSurface:
    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_privilege(self, mock_run: MagicMock) -> None:
        """_collect_privilege_surface should collect security info."""
        # getenforce -> disabled, aa-status -> disabled
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="Disabled\n"),  # getenforce
            MagicMock(returncode=1, stdout=""),  # aa-status
        ]
        result = _collect_privilege_surface()
        assert isinstance(result, PrivilegeSurface)
        assert result.selinux_mode == "disabled"
        assert result.apparmor_mode == "disabled"

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_privilege_apparmor_enabled(self, mock_run: MagicMock) -> None:
        """_collect_privilege_surface should detect apparmor enabled."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),  # getenforce fails
            MagicMock(returncode=0, stdout="Yes"),  # aa-status
        ]
        result = _collect_privilege_surface()
        assert result.apparmor_mode == "enabled"

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_collect_privilege_all_exceptions(self, mock_run: MagicMock) -> None:
        """_collect_privilege_surface should handle all exceptions."""
        mock_run.side_effect = Exception("no such command")
        result = _collect_privilege_surface()
        assert isinstance(result, PrivilegeSurface)


# ---------------------------------------------------------------------------
# Extended coverage: _collect_network_control_surface
# ---------------------------------------------------------------------------


class TestCollectNetworkControlSurface:
    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    @patch("elle.daemon.incidents.control_surface.Path")
    def test_network_networkmanager(self, mock_path_cls: MagicMock, mock_run: MagicMock) -> None:
        """Should detect NetworkManager."""
        # Create mock path instances
        def path_side_effect(path_str):
            mock_p = MagicMock()
            if path_str == "/run/NetworkManager/NetworkManager.pid":
                mock_p.exists.return_value = True
            elif path_str == "/etc/resolv.conf":
                mock_p.is_symlink.return_value = False
                mock_p.exists.return_value = True
            else:
                mock_p.exists.return_value = False
                mock_p.is_dir.return_value = False
                mock_p.is_symlink.return_value = False
            return mock_p

        mock_path_cls.side_effect = path_side_effect
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = _collect_network_control_surface()
        assert isinstance(result, NetworkControlSurface)

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_network_all_fail(self, mock_run: MagicMock) -> None:
        """Should handle all subprocess failures."""
        mock_run.side_effect = Exception("no such command")
        result = _collect_network_control_surface()
        assert isinstance(result, NetworkControlSurface)


# ---------------------------------------------------------------------------
# Extended coverage: _collect_kernel_policy_surface
# ---------------------------------------------------------------------------


class TestCollectKernelPolicySurface:
    def test_kernel_version_detected(self) -> None:
        """_collect_kernel_policy_surface should detect kernel version."""
        result = _collect_kernel_policy_surface()
        assert result.kernel_version != ""


# ---------------------------------------------------------------------------
# Extended coverage: _collect_hardware_identity_surface
# ---------------------------------------------------------------------------


class TestCollectHardwareIdentitySurface:
    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_hardware_with_lsblk(self, mock_run: MagicMock) -> None:
        """_collect_hardware_identity_surface should collect lsblk info."""
        mock_run.return_value = MagicMock(returncode=0, stdout='{"blockdevices": []}')
        result = _collect_hardware_identity_surface()
        assert isinstance(result, HardwareIdentitySurface)
        assert result.cpu_cores > 0

    @patch("elle.daemon.incidents.control_surface.subprocess.run")
    def test_hardware_all_fail(self, mock_run: MagicMock) -> None:
        """_collect_hardware_identity_surface should handle all failures."""
        mock_run.side_effect = Exception("fail")
        result = _collect_hardware_identity_surface()
        assert isinstance(result, HardwareIdentitySurface)
        assert result.disk_layout_hash == ""


# ---------------------------------------------------------------------------
# Extended coverage: surface_hash methods edge cases
# ---------------------------------------------------------------------------


class TestSurfaceHashEdgeCases:
    def test_config_file_surface_empty_sha256(self) -> None:
        """ConfigFileSurface with empty sha256 should return empty hash."""
        cfg = ConfigFileSurface(path="/etc/test", sha256="")
        assert cfg.surface_hash() == ""

    def test_package_surface_with_hash_value(self) -> None:
        """PackageSurface with pre-computed hash should use it."""
        surface = PackageSurface(
            packages=(),
            surface_hash_value="abcdef1234567890abcdef1234567890",
        )
        assert surface.surface_hash() == "abcdef1234567890"

    def test_package_surface_computes_hash(self) -> None:
        """PackageSurface without pre-computed hash should compute one."""
        pkgs = (
            PackageInfo(name="a", version="1.0"),
            PackageInfo(name="b", version="2.0"),
        )
        surface = PackageSurface(packages=pkgs, surface_hash_value="")
        h = surface.surface_hash()
        assert len(h) == 16

    def test_systemd_service_surface_hash_with_hash(self) -> None:
        """SystemdServiceSurface with effective_hash should use it directly."""
        svc = SystemdServiceSurface(
            unit_name="test.service",
            effective_hash="myhash123",
        )
        assert svc.surface_hash() == "myhash123"

    def test_kernel_policy_surface_hash_with_sysctls(self) -> None:
        """KernelPolicySurface hash should include sysctls."""
        k1 = KernelPolicySurface(
            key_sysctls={"vm.swappiness": "60", "kernel.panic": "10"},
        )
        k2 = KernelPolicySurface(
            key_sysctls={"vm.swappiness": "30", "kernel.panic": "10"},
        )
        assert k1.surface_hash() != k2.surface_hash()


# ---------------------------------------------------------------------------
# Extended coverage: collect_control_surface with explicit args
# ---------------------------------------------------------------------------


class TestCollectControlSurfaceExtended:
    @patch("elle.daemon.incidents.control_surface._collect_service_surface")
    @patch("elle.daemon.incidents.control_surface._collect_config_surface")
    @patch("elle.daemon.incidents.control_surface._collect_package_surface")
    @patch("elle.daemon.incidents.control_surface._collect_scheduler_surface")
    @patch("elle.daemon.incidents.control_surface._collect_privilege_surface")
    @patch("elle.daemon.incidents.control_surface._collect_network_control_surface")
    @patch("elle.daemon.incidents.control_surface._collect_kernel_policy_surface")
    @patch("elle.daemon.incidents.control_surface._collect_hardware_identity_surface")
    def test_collect_with_all_args(
        self,
        mock_hw,
        mock_kernel,
        mock_net,
        mock_priv,
        mock_sched,
        mock_pkg,
        mock_cfg,
        mock_svc,
    ) -> None:
        """collect_control_surface should use provided services and config_paths."""
        mock_svc.return_value = SystemdServiceSurface(unit_name="nginx.service", enabled=True)
        mock_cfg.return_value = ConfigFileSurface(path="/etc/custom.conf", sha256="abc" * 22)
        mock_pkg.return_value = PackageSurface()
        mock_sched.return_value = SchedulerSurface()
        mock_priv.return_value = PrivilegeSurface()
        mock_net.return_value = NetworkControlSurface()
        mock_kernel.return_value = KernelPolicySurface()
        mock_hw.return_value = HardwareIdentitySurface()

        snap = collect_control_surface(
            services=["nginx.service"],
            config_paths=["/etc/custom.conf"],
            packages=["nginx"],
        )
        assert isinstance(snap, ControlSurfaceSnapshot)
        assert len(snap.services) == 1

    @patch("elle.daemon.incidents.control_surface._collect_service_surface")
    def test_collect_skips_none_services(self, mock_svc) -> None:
        """collect_control_surface should skip services that return None."""
        mock_svc.return_value = None
        snap = collect_control_surface(services=["bad.service"])
        assert len(snap.services) == 0
