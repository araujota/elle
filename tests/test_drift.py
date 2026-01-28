"""Tests for surface drift detection."""

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
)
from elle.daemon.incidents.drift import (
    DriftDetail,
    detect_surface_drift,
    explain_drift,
    get_drifted_surfaces,
    has_any_drift,
    summarize_drift,
)


def make_snapshot(**overrides) -> ControlSurfaceSnapshot:
    """Create a test snapshot with optional overrides."""
    defaults = {
        "services": (),
        "configs": (),
        "packages": PackageSurface(packages=()),
        "scheduler": SchedulerSurface(),
        "privilege": PrivilegeSurface(),
        "network_control": NetworkControlSurface(),
        "kernel_policy": KernelPolicySurface(kernel_version="5.15.0"),
        "hardware": HardwareIdentitySurface(cpu_cores=4),
    }
    defaults.update(overrides)
    return ControlSurfaceSnapshot(**defaults)


class TestDetectSurfaceDrift:
    """Tests for detect_surface_drift function."""

    def test_no_drift_identical_snapshots(self) -> None:
        """Identical snapshots should show no drift."""
        snap1 = make_snapshot()
        snap2 = make_snapshot()
        drift = detect_surface_drift(snap1, snap2)
        assert not any(drift.values())

    def test_drift_on_kernel_change(self) -> None:
        """Kernel version change should be detected."""
        snap1 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.15.0"))
        snap2 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.16.0"))
        drift = detect_surface_drift(snap1, snap2)
        assert drift.get("kernel_policy") is True

    def test_drift_on_hardware_change(self) -> None:
        """Hardware change should be detected."""
        snap1 = make_snapshot(hardware=HardwareIdentitySurface(cpu_cores=4))
        snap2 = make_snapshot(hardware=HardwareIdentitySurface(cpu_cores=8))
        drift = detect_surface_drift(snap1, snap2)
        assert drift.get("hardware") is True

    def test_drift_on_package_change(self) -> None:
        """Package change should be detected."""
        pkgs1 = PackageSurface(packages=(PackageInfo(name="nginx", version="1.18.0"),))
        pkgs2 = PackageSurface(packages=(PackageInfo(name="nginx", version="1.20.0"),))
        snap1 = make_snapshot(packages=pkgs1)
        snap2 = make_snapshot(packages=pkgs2)
        drift = detect_surface_drift(snap1, snap2)
        assert drift.get("packages") is True

    def test_drift_on_service_change(self) -> None:
        """Service config change should be detected."""
        svc1 = (SystemdServiceSurface(unit_name="nginx.service", enabled=True),)
        svc2 = (SystemdServiceSurface(unit_name="nginx.service", enabled=False),)
        snap1 = make_snapshot(services=svc1)
        snap2 = make_snapshot(services=svc2)
        drift = detect_surface_drift(snap1, snap2)
        assert drift.get("service:nginx.service") is True

    def test_drift_on_config_change(self) -> None:
        """Config file change should be detected."""
        cfg1 = (ConfigFileSurface(path="/etc/nginx", sha256="aaa" * 22),)
        cfg2 = (ConfigFileSurface(path="/etc/nginx", sha256="bbb" * 22),)
        snap1 = make_snapshot(configs=cfg1)
        snap2 = make_snapshot(configs=cfg2)
        drift = detect_surface_drift(snap1, snap2)
        assert drift.get("config:/etc/nginx") is True

    def test_drift_on_new_service(self) -> None:
        """New service should be detected as drift."""
        snap1 = make_snapshot(services=())
        snap2 = make_snapshot(
            services=(SystemdServiceSurface(unit_name="new.service", enabled=True),)
        )
        drift = detect_surface_drift(snap1, snap2)
        assert drift.get("service:new.service") is True


class TestGetDriftedSurfaces:
    """Tests for get_drifted_surfaces function."""

    def test_returns_empty_for_no_drift(self) -> None:
        """Should return empty list when no drift."""
        snap1 = make_snapshot()
        snap2 = make_snapshot()
        drifted = get_drifted_surfaces(snap1, snap2)
        assert drifted == []

    def test_returns_drifted_surface_names(self) -> None:
        """Should return list of drifted surface names."""
        snap1 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.15.0"))
        snap2 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.16.0"))
        drifted = get_drifted_surfaces(snap1, snap2)
        assert "kernel_policy" in drifted


class TestExplainDrift:
    """Tests for explain_drift function."""

    def test_explain_kernel_drift(self) -> None:
        """Should explain kernel version change."""
        snap1 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.15.0"))
        snap2 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.16.0"))
        details = explain_drift(snap1, snap2)
        assert len(details) == 1
        assert isinstance(details[0], DriftDetail)
        assert "kernel" in details[0].explanation.lower()
        assert "5.16.0" in details[0].explanation

    def test_explain_service_enabled_change(self) -> None:
        """Should explain service enable state change."""
        svc1 = (SystemdServiceSurface(unit_name="nginx.service", enabled=True),)
        svc2 = (SystemdServiceSurface(unit_name="nginx.service", enabled=False),)
        snap1 = make_snapshot(services=svc1)
        snap2 = make_snapshot(services=svc2)
        details = explain_drift(snap1, snap2)
        assert len(details) == 1
        assert "disabled" in details[0].explanation.lower()

    def test_explain_package_drift(self) -> None:
        """Should explain package changes."""
        pkgs1 = PackageSurface(packages=(PackageInfo(name="nginx", version="1.18.0"),))
        pkgs2 = PackageSurface(packages=(PackageInfo(name="nginx", version="1.20.0"),))
        snap1 = make_snapshot(packages=pkgs1)
        snap2 = make_snapshot(packages=pkgs2)
        details = explain_drift(snap1, snap2)
        assert len(details) == 1
        assert "upgraded" in details[0].explanation.lower()

    def test_explain_with_specific_surfaces(self) -> None:
        """Should only explain specified surfaces."""
        snap1 = make_snapshot(
            kernel_policy=KernelPolicySurface(kernel_version="5.15.0"),
            hardware=HardwareIdentitySurface(cpu_cores=4),
        )
        snap2 = make_snapshot(
            kernel_policy=KernelPolicySurface(kernel_version="5.16.0"),
            hardware=HardwareIdentitySurface(cpu_cores=8),
        )
        # Only explain kernel_policy
        details = explain_drift(snap1, snap2, drifted_surfaces=["kernel_policy"])
        assert len(details) == 1
        assert details[0].surface == "kernel_policy"


class TestSummarizeDrift:
    """Tests for summarize_drift function."""

    def test_no_drift_summary(self) -> None:
        """Should indicate no drift detected."""
        snap1 = make_snapshot()
        snap2 = make_snapshot()
        summary = summarize_drift(snap1, snap2)
        assert "no" in summary.lower() or "No" in summary

    def test_single_drift_summary(self) -> None:
        """Should summarize single drift."""
        snap1 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.15.0"))
        snap2 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.16.0"))
        summary = summarize_drift(snap1, snap2)
        assert "kernel_policy" in summary

    def test_multiple_drift_summary(self) -> None:
        """Should summarize multiple drifts."""
        svc1 = (SystemdServiceSurface(unit_name="nginx.service", enabled=True),)
        svc2 = (SystemdServiceSurface(unit_name="nginx.service", enabled=False),)
        cfg1 = (ConfigFileSurface(path="/etc/nginx", sha256="aaa" * 22),)
        cfg2 = (ConfigFileSurface(path="/etc/nginx", sha256="bbb" * 22),)
        snap1 = make_snapshot(services=svc1, configs=cfg1)
        snap2 = make_snapshot(services=svc2, configs=cfg2)
        summary = summarize_drift(snap1, snap2)
        assert "service" in summary.lower()
        assert "config" in summary.lower()


class TestHasAnyDrift:
    """Tests for has_any_drift function."""

    def test_no_drift(self) -> None:
        """Should return False for identical snapshots."""
        snap1 = make_snapshot()
        snap2 = make_snapshot()
        assert has_any_drift(snap1, snap2) is False

    def test_has_drift(self) -> None:
        """Should return True when drift exists."""
        snap1 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.15.0"))
        snap2 = make_snapshot(kernel_policy=KernelPolicySurface(kernel_version="5.16.0"))
        assert has_any_drift(snap1, snap2) is True
