"""Tests for control_surface.py to boost coverage.

Tests surface hash computation, collection functions, and model instantiation.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

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

# ---------------------------------------------------------------------------
# Surface hash tests
# ---------------------------------------------------------------------------


class TestSurfaceHashes:
    @pytest.mark.timeout(10)
    def test_systemd_service_with_effective_hash(self):
        svc = SystemdServiceSurface(unit_name="nginx.service", effective_hash="abc123def456")
        assert svc.surface_hash() == "abc123def456"

    @pytest.mark.timeout(10)
    def test_systemd_service_without_effective_hash(self):
        svc = SystemdServiceSurface(unit_name="nginx.service", enabled=True, restart_policy="always")
        h = svc.surface_hash()
        assert len(h) == 16
        # Should be deterministic
        assert svc.surface_hash() == h

    @pytest.mark.timeout(10)
    def test_config_file_surface_hash(self):
        cfg = ConfigFileSurface(path="/etc/hosts", sha256="abcdef1234567890abcdef1234567890")
        assert cfg.surface_hash() == "abcdef1234567890"[:16]

    @pytest.mark.timeout(10)
    def test_config_file_surface_hash_empty(self):
        cfg = ConfigFileSurface(path="/etc/hosts", sha256="")
        assert cfg.surface_hash() == ""

    @pytest.mark.timeout(10)
    def test_package_surface_hash_with_value(self):
        ps = PackageSurface(surface_hash_value="abcdef1234567890abcdef")
        assert ps.surface_hash() == "abcdef1234567890"

    @pytest.mark.timeout(10)
    def test_package_surface_hash_computed(self):
        pkgs = (
            PackageInfo(name="nginx", version="1.18"),
            PackageInfo(name="python3", version="3.10"),
        )
        ps = PackageSurface(packages=pkgs)
        h = ps.surface_hash()
        assert len(h) == 16

        # Verify computation
        pairs = sorted(f"{p.name}:{p.version}" for p in pkgs)
        expected = hashlib.sha256("\n".join(pairs).encode()).hexdigest()[:16]
        assert h == expected

    @pytest.mark.timeout(10)
    def test_scheduler_surface_hash(self):
        ss = SchedulerSurface(systemd_timers_hash="abc", cron_entries_hash="def", unattended_upgrades=True)
        h = ss.surface_hash()
        assert len(h) == 16

    @pytest.mark.timeout(10)
    def test_privilege_surface_hash(self):
        ps = PrivilegeSurface(sudoers_hash="abc", groups_hash="def")
        h = ps.surface_hash()
        assert len(h) == 16

    @pytest.mark.timeout(10)
    def test_network_control_surface_hash(self):
        nc = NetworkControlSurface(network_manager="NetworkManager", firewall_frontend="ufw")
        h = nc.surface_hash()
        assert len(h) == 16

    @pytest.mark.timeout(10)
    def test_kernel_policy_surface_hash(self):
        kp = KernelPolicySurface(
            kernel_version="6.8.0",
            cgroup_mode="v2",
            key_sysctls={"vm.swappiness": "60"},
            swap_enabled=True,
        )
        h = kp.surface_hash()
        assert len(h) == 16

    @pytest.mark.timeout(10)
    def test_hardware_identity_surface_hash(self):
        hw = HardwareIdentitySurface(
            disk_layout_hash="abc",
            filesystem_types=("ext4", "xfs"),
            total_memory_mb=16384,
            cpu_cores=8,
            virtualization="kvm",
        )
        h = hw.surface_hash()
        assert len(h) == 16


# ---------------------------------------------------------------------------
# ControlSurfaceSnapshot.surface_hashes tests
# ---------------------------------------------------------------------------


class TestControlSurfaceSnapshotHashes:
    @pytest.mark.timeout(10)
    def test_surface_hashes(self):
        svc = SystemdServiceSurface(unit_name="nginx.service", effective_hash="svc_hash_abc")
        cfg = ConfigFileSurface(path="/etc/hosts", sha256="cfg_hash_abcdef1234567890")
        snap = ControlSurfaceSnapshot(
            services=(svc,),
            configs=(cfg,),
            packages=PackageSurface(),
            scheduler=SchedulerSurface(),
            privilege=PrivilegeSurface(),
            network_control=NetworkControlSurface(),
            kernel_policy=KernelPolicySurface(),
            hardware=HardwareIdentitySurface(),
        )

        hashes = snap.surface_hashes()
        assert "packages" in hashes
        assert "scheduler" in hashes
        assert "privilege" in hashes
        assert "network_control" in hashes
        assert "kernel_policy" in hashes
        assert "hardware" in hashes
        assert "service:nginx.service" in hashes
        assert "config:/etc/hosts" in hashes


# ---------------------------------------------------------------------------
# _hash_file / _hash_directory tests
# ---------------------------------------------------------------------------


class TestHashFunctions:
    @pytest.mark.timeout(10)
    def test_hash_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h = _hash_file(f)
        assert h == hashlib.sha256(b"hello world").hexdigest()

    @pytest.mark.timeout(10)
    def test_hash_file_missing(self, tmp_path):
        f = tmp_path / "missing.txt"
        assert _hash_file(f) == ""

    @pytest.mark.timeout(10)
    def test_hash_directory(self, tmp_path):
        d = tmp_path / "configs"
        d.mkdir()
        (d / "a.conf").write_text("aaa")
        (d / "b.conf").write_text("bbb")

        h = _hash_directory(d)
        assert len(h) == 64  # Full SHA256 hex

    @pytest.mark.timeout(10)
    def test_hash_directory_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("not a dir")
        assert _hash_directory(f) == ""

    @pytest.mark.timeout(10)
    def test_hash_directory_missing(self, tmp_path):
        assert _hash_directory(tmp_path / "missing") == ""


# ---------------------------------------------------------------------------
# _collect_config_surface tests
# ---------------------------------------------------------------------------


class TestCollectConfigSurface:
    @pytest.mark.timeout(10)
    def test_file(self, tmp_path):
        f = tmp_path / "nginx.conf"
        f.write_text("worker_processes auto;")

        result = _collect_config_surface(str(f))
        assert result is not None
        assert result.path == str(f)
        assert result.sha256 != ""
        assert result.fragment_count == 0

    @pytest.mark.timeout(10)
    def test_directory(self, tmp_path):
        d = tmp_path / "nginx"
        d.mkdir()
        (d / "server.conf").write_text("server block")

        result = _collect_config_surface(str(d))
        assert result is not None
        assert result.path == str(d)

    @pytest.mark.timeout(10)
    def test_nonexistent(self):
        result = _collect_config_surface("/nonexistent/path")
        assert result is None


# ---------------------------------------------------------------------------
# _collect_service_surface tests
# ---------------------------------------------------------------------------


class TestCollectServiceSurface:
    @pytest.mark.timeout(10)
    def test_active_service(self):
        show_result = MagicMock()
        show_result.returncode = 0
        show_result.stdout = "UnitFileState=enabled\nActiveState=active\nRestart=always\nUser=www-data\nGroup=www-data\nCPUQuota=50%\nMemoryMax=1G\n"

        cat_result = MagicMock()
        cat_result.returncode = 0
        cat_result.stdout = "[Service]\nExecStart=/usr/sbin/nginx\n"

        def run_side_effect(cmd, **kwargs):
            if "show" in cmd:
                return show_result
            elif "cat" in cmd:
                return cat_result
            return MagicMock(returncode=1)

        with patch("elle.daemon.incidents.control_surface.subprocess.run", side_effect=run_side_effect):
            with patch("elle.daemon.incidents.control_surface.Path") as MockPath:
                MockPath.return_value.is_dir.return_value = False
                result = _collect_service_surface("nginx.service")

        assert result is not None
        assert result.unit_name == "nginx.service"
        assert result.enabled is True
        assert result.active_state == "active"

    @pytest.mark.timeout(10)
    def test_service_failure(self):
        show_result = MagicMock()
        show_result.returncode = 4

        with patch("elle.daemon.incidents.control_surface.subprocess.run", return_value=show_result):
            result = _collect_service_surface("nonexistent.service")

        assert result is None

    @pytest.mark.timeout(10)
    def test_exception(self):
        with patch("elle.daemon.incidents.control_surface.subprocess.run", side_effect=Exception("fail")):
            result = _collect_service_surface("bad.service")

        assert result is None


# ---------------------------------------------------------------------------
# _collect_package_surface tests
# ---------------------------------------------------------------------------


class TestCollectPackageSurface:
    @pytest.mark.timeout(10)
    def test_with_packages(self):
        dpkg_result = MagicMock()
        dpkg_result.returncode = 0
        dpkg_result.stdout = "1.24.0-1ubuntu1"

        auto_result = MagicMock()
        auto_result.returncode = 0
        auto_result.stdout = ""

        def run_side_effect(cmd, **kwargs):
            if "dpkg-query" in cmd:
                return dpkg_result
            elif "apt-mark" in cmd:
                return auto_result
            return MagicMock(returncode=1)

        with patch("elle.daemon.incidents.control_surface.subprocess.run", side_effect=run_side_effect):
            result = _collect_package_surface(["nginx"])

        assert isinstance(result, PackageSurface)
        assert len(result.packages) >= 1

    @pytest.mark.timeout(10)
    def test_default_packages(self):
        result_mock = MagicMock()
        result_mock.returncode = 1
        result_mock.stdout = ""

        with patch("elle.daemon.incidents.control_surface.subprocess.run", return_value=result_mock):
            result = _collect_package_surface()

        assert isinstance(result, PackageSurface)


# ---------------------------------------------------------------------------
# Other collection tests
# ---------------------------------------------------------------------------


class TestCollectSchedulerSurface:
    @pytest.mark.timeout(10)
    def test_basic(self):
        timer_result = MagicMock()
        timer_result.returncode = 0
        timer_result.stdout = "NEXT  LEFT  LAST  PASSED  UNIT  ACTIVATES\n"

        with patch("elle.daemon.incidents.control_surface.subprocess.run", return_value=timer_result):
            with patch("elle.daemon.incidents.control_surface.Path") as MockPath:
                MockPath.return_value.is_dir.return_value = False
                MockPath.return_value.exists.return_value = False
                result = _collect_scheduler_surface()

        assert isinstance(result, SchedulerSurface)


class TestCollectKernelPolicySurface:
    @pytest.mark.timeout(10)
    def test_basic(self):
        with patch("elle.daemon.incidents.control_surface.Path") as MockPath:
            MockPath.return_value.exists.return_value = False
            MockPath.return_value.is_dir.return_value = False
            MockPath.return_value.read_text.side_effect = FileNotFoundError
            result = _collect_kernel_policy_surface()

        assert isinstance(result, KernelPolicySurface)


class TestCollectNetworkControlSurface:
    @pytest.mark.timeout(10)
    def test_basic(self):
        with patch("elle.daemon.incidents.control_surface.Path") as MockPath:
            MockPath.return_value.exists.return_value = False
            MockPath.return_value.is_dir.return_value = False
            MockPath.return_value.is_symlink.return_value = False
            with patch("elle.daemon.incidents.control_surface.subprocess.run", side_effect=Exception):
                result = _collect_network_control_surface()

        assert isinstance(result, NetworkControlSurface)


class TestCollectHardwareIdentitySurface:
    @pytest.mark.timeout(10)
    def test_basic(self):
        with patch("elle.daemon.incidents.control_surface.subprocess.run", side_effect=Exception):
            with patch("elle.daemon.incidents.control_surface.Path") as MockPath:
                MockPath.return_value.read_text.side_effect = FileNotFoundError
                with patch("elle.daemon.incidents.control_surface.os.cpu_count", return_value=4):
                    result = _collect_hardware_identity_surface()

        assert isinstance(result, HardwareIdentitySurface)
        assert result.cpu_cores == 4


class TestCollectPrivilegeSurface:
    @pytest.mark.timeout(10)
    def test_basic(self):
        with patch("elle.daemon.incidents.control_surface.Path") as MockPath:
            MockPath.return_value.exists.return_value = False
            MockPath.return_value.is_dir.return_value = False
            with patch("elle.daemon.incidents.control_surface.subprocess.run", side_effect=Exception):
                result = _collect_privilege_surface()

        assert isinstance(result, PrivilegeSurface)


# ---------------------------------------------------------------------------
# collect_control_surface integration
# ---------------------------------------------------------------------------


class TestCollectControlSurface:
    @pytest.mark.timeout(10)
    def test_basic(self):
        with patch("elle.daemon.incidents.control_surface._collect_service_surface", return_value=None):
            with patch("elle.daemon.incidents.control_surface._collect_config_surface", return_value=None):
                with patch("elle.daemon.incidents.control_surface._collect_package_surface") as pkg:
                    pkg.return_value = PackageSurface()
                    with patch("elle.daemon.incidents.control_surface._collect_scheduler_surface") as sched:
                        sched.return_value = SchedulerSurface()
                        with patch("elle.daemon.incidents.control_surface._collect_privilege_surface") as priv:
                            priv.return_value = PrivilegeSurface()
                            with patch("elle.daemon.incidents.control_surface._collect_network_control_surface") as net:
                                net.return_value = NetworkControlSurface()
                                with patch(
                                    "elle.daemon.incidents.control_surface._collect_kernel_policy_surface"
                                ) as kern:
                                    kern.return_value = KernelPolicySurface()
                                    with patch(
                                        "elle.daemon.incidents.control_surface._collect_hardware_identity_surface"
                                    ) as hw:
                                        hw.return_value = HardwareIdentitySurface()
                                        result = collect_control_surface(
                                            services=["nginx.service"],
                                            config_paths=["/etc/hosts"],
                                        )

        assert isinstance(result, ControlSurfaceSnapshot)
