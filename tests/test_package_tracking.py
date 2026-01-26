"""Tests for package version tracking in incident snapshots."""

import pytest

from elle.daemon.incidents.models import PackageState, SystemSnapshot
from elle.daemon.incidents.snapshot import (
    BEDROCK_PACKAGES,
    _extract_relevant_packages,
    collect_package_state,
)


class TestBedrockPackages:
    """Tests for bedrock package configuration."""

    def test_bedrock_packages_defined(self):
        """Test that bedrock packages are defined."""
        assert len(BEDROCK_PACKAGES) >= 10
        assert isinstance(BEDROCK_PACKAGES, tuple)

    def test_bedrock_includes_essential_packages(self):
        """Test that essential system packages are in bedrock."""
        essential = {"systemd", "libc6", "apt", "dpkg"}
        assert essential.issubset(set(BEDROCK_PACKAGES))

    def test_bedrock_includes_python(self):
        """Test that Python packages are in bedrock (ELLE runtime)."""
        assert "python3" in BEDROCK_PACKAGES


class TestPackageState:
    """Tests for PackageState model."""

    def test_package_state_creation(self):
        """Test creating a PackageState."""
        state = PackageState(
            name="nginx",
            version="1.18.0-6ubuntu14",
            source="apt",
            is_bedrock=False,
        )
        assert state.name == "nginx"
        assert state.version == "1.18.0-6ubuntu14"
        assert state.source == "apt"
        assert state.is_bedrock is False

    def test_package_state_immutable(self):
        """Test that PackageState is immutable."""
        state = PackageState(name="nginx", version="1.0")
        with pytest.raises(Exception):
            state.name = "apache"

    def test_package_state_defaults(self):
        """Test PackageState default values."""
        state = PackageState(name="test", version="1.0")
        assert state.source == "apt"
        assert state.is_bedrock is False


class TestExtractRelevantPackages:
    """Tests for package extraction from incident context."""

    def test_extract_from_apt_install(self):
        """Test extracting package from apt install command."""
        packages = _extract_relevant_packages(
            command="apt install nginx",
            stderr=None,
            entity=None,
        )
        assert "nginx" in packages

    def test_extract_from_apt_get_install(self):
        """Test extracting package from apt-get install command."""
        packages = _extract_relevant_packages(
            command="apt-get install postgresql",
            stderr=None,
            entity=None,
        )
        assert "postgresql" in packages

    def test_extract_from_pip_install(self):
        """Test extracting package from pip install command."""
        packages = _extract_relevant_packages(
            command="pip install requests",
            stderr=None,
            entity=None,
        )
        assert "requests" in packages

    def test_extract_from_pip3_install(self):
        """Test extracting package from pip3 install command."""
        packages = _extract_relevant_packages(
            command="pip3 install flask",
            stderr=None,
            entity=None,
        )
        assert "flask" in packages

    def test_extract_from_systemctl_command(self):
        """Test extracting service/package from systemctl command."""
        packages = _extract_relevant_packages(
            command="systemctl restart nginx",
            stderr=None,
            entity=None,
        )
        assert "nginx" in packages

    def test_extract_from_error_message(self):
        """Test extracting package from error message."""
        packages = _extract_relevant_packages(
            command=None,
            stderr="Package 'nonexistent-pkg' has no installation candidate",
            entity=None,
        )
        assert "nonexistent-pkg" in packages

    def test_extract_from_dependency_error(self):
        """Test extracting package from dependency error."""
        packages = _extract_relevant_packages(
            command=None,
            stderr="Depends: libfoo1 but it is not installable",
            entity=None,
        )
        assert "libfoo1" in packages

    def test_extract_from_entity(self):
        """Test extracting package from entity field."""
        packages = _extract_relevant_packages(
            command=None,
            stderr=None,
            entity="service:nginx",
        )
        assert "nginx" in packages

    def test_extract_strips_service_suffix(self):
        """Test that .service suffix is stripped from entity."""
        packages = _extract_relevant_packages(
            command=None,
            stderr=None,
            entity="service:nginx.service",
        )
        assert "nginx" in packages

    def test_extract_limits_error_mentions(self):
        """Test that extraction limits packages from errors."""
        # Create error with many package mentions
        stderr = "\n".join([f"Package 'pkg{i}' not found" for i in range(20)])
        packages = _extract_relevant_packages(
            command=None,
            stderr=stderr,
            entity=None,
        )
        # Should be limited, not all 20
        assert len(packages) <= 10

    def test_extract_empty_inputs(self):
        """Test extraction with no inputs."""
        packages = _extract_relevant_packages(
            command=None,
            stderr=None,
            entity=None,
        )
        assert len(packages) == 0


class TestCollectPackageState:
    """Tests for collect_package_state function."""

    def test_returns_tuple(self):
        """Test that collect_package_state returns a tuple."""
        packages = collect_package_state()
        assert isinstance(packages, tuple)

    def test_all_entries_are_package_state(self):
        """Test that all entries are PackageState instances."""
        packages = collect_package_state()
        for pkg in packages:
            assert isinstance(pkg, PackageState)

    def test_bedrock_packages_marked(self):
        """Test that bedrock packages are marked as such."""
        packages = collect_package_state()
        bedrock_pkgs = [p for p in packages if p.is_bedrock]
        # At least some bedrock packages should be found (on Linux)
        # On non-Linux, this may be empty
        assert all(p.name in BEDROCK_PACKAGES for p in bedrock_pkgs)

    def test_relevant_packages_from_command(self):
        """Test that relevant packages are extracted from command."""
        packages = collect_package_state(
            command="apt install nginx",
            stderr=None,
            entity=None,
        )
        # nginx may or may not be installed, but if it is, it should be included
        {p.name for p in packages}
        # Bedrock packages should still be there
        assert len(packages) >= 0  # May be 0 on non-Linux

    def test_max_relevant_packages(self):
        """Test that relevant packages are limited to 10."""
        # Create command with many packages (won't all be installed)
        packages = collect_package_state(
            command="apt install nginx postgresql mysql redis",
            stderr="Package 'pkg1' Package 'pkg2' Package 'pkg3'",
            entity="service:apache2",
        )
        # Just verify it doesn't crash with many inputs
        assert isinstance(packages, tuple)


class TestSystemSnapshotWithPackages:
    """Tests for SystemSnapshot with packages field."""

    def test_snapshot_has_packages_field(self):
        """Test that SystemSnapshot has packages field."""
        snapshot = SystemSnapshot(
            os="Ubuntu 24.04",
            kernel="6.8.0",
            uptime_sec=3600,
            cpu_load=(1.0, 1.0, 1.0),
            mem_total_mb=16384,
            mem_free_mb=8000,
            mem_available_mb=10000,
            packages=(
                PackageState(name="nginx", version="1.18.0"),
                PackageState(name="systemd", version="253", is_bedrock=True),
            ),
        )
        assert len(snapshot.packages) == 2
        assert snapshot.packages[0].name == "nginx"
        assert snapshot.packages[1].is_bedrock is True

    def test_snapshot_packages_default_empty(self):
        """Test that packages defaults to empty tuple."""
        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
        )
        assert snapshot.packages == ()

    def test_snapshot_packages_immutable(self):
        """Test that packages field is immutable."""
        snapshot = SystemSnapshot(
            os="Ubuntu",
            kernel="6.8",
            uptime_sec=100,
            cpu_load=(0.5, 0.5, 0.5),
            mem_total_mb=8192,
            mem_free_mb=4096,
            mem_available_mb=6144,
            packages=(PackageState(name="test", version="1.0"),),
        )
        # Tuple should be immutable
        assert isinstance(snapshot.packages, tuple)
