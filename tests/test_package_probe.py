"""Tests for the PackageProbe telemetry module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from elle.daemon.telemetry.package_probe import PackageProbe


@pytest.fixture
def probe():
    """Create a PackageProbe instance."""
    return PackageProbe()


@pytest.fixture
def sample_dpkg_status():
    """Create sample dpkg status content."""
    return """Package: nginx
Status: install ok installed
Priority: optional
Section: httpd
Installed-Size: 1234
Maintainer: Ubuntu Developers
Architecture: amd64
Version: 1.24.0-1ubuntu1
Depends: libc6, libpcre3

Package: systemd
Status: install ok installed
Priority: required
Section: admin
Installed-Size: 5678
Maintainer: Ubuntu Developers
Architecture: amd64
Version: 255.4-1ubuntu8
Depends: libsystemd0

Package: removed-package
Status: deinstall ok config-files
Priority: optional
Section: misc
Installed-Size: 100
Maintainer: Ubuntu Developers
Architecture: amd64
Version: 1.0.0-1
"""


class TestPackageProbe:
    """Tests for PackageProbe class."""

    def test_init(self, probe):
        """Test probe initialization."""
        assert probe.name == "package"
        assert probe.interval == 300
        assert len(probe._watched_packages) == 0
        assert not probe._initialized

    def test_set_watched_packages(self, probe):
        """Test setting watched packages."""
        packages = {"nginx", "systemd", "docker.io"}
        probe.set_watched_packages(packages)

        assert probe._watched_packages == packages

    def test_add_watched_package(self, probe):
        """Test adding a watched package."""
        probe.add_watched_package("nginx")
        probe.add_watched_package("systemd")

        assert "nginx" in probe._watched_packages
        assert "systemd" in probe._watched_packages

    def test_remove_watched_package(self, probe):
        """Test removing a watched package."""
        probe.set_watched_packages({"nginx", "systemd"})
        probe._package_versions = {"nginx": "1.24.0", "systemd": "255.4"}

        probe.remove_watched_package("nginx")

        assert "nginx" not in probe._watched_packages
        assert "nginx" not in probe._package_versions
        assert "systemd" in probe._watched_packages

    @pytest.mark.asyncio
    async def test_run_no_watched_packages(self, probe):
        """Test run with no watched packages."""
        result = await probe.run()

        assert result.success is True
        assert result.data["watched_packages"] == 0
        assert len(result.events) == 0

    @pytest.mark.asyncio
    async def test_run_first_run_baseline(self, probe, sample_dpkg_status):
        """Test first run establishes baseline."""
        probe.set_watched_packages({"nginx", "systemd"})

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(sample_dpkg_status)
            f.flush()
            probe.DPKG_STATUS_PATH = Path(f.name)

            result = await probe.run()

            assert result.success is True
            assert probe._initialized is True
            assert probe._package_versions.get("nginx") == "1.24.0-1ubuntu1"
            assert probe._package_versions.get("systemd") == "255.4-1ubuntu8"
            assert len(result.events) == 0  # No events on first run

            Path(f.name).unlink()

    @pytest.mark.asyncio
    async def test_run_detects_upgrade(self, probe, sample_dpkg_status):
        """Test run detects package upgrade."""
        probe.set_watched_packages({"nginx"})
        probe._initialized = True
        probe._package_versions = {"nginx": "1.22.0-1ubuntu1"}  # Old version

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(sample_dpkg_status)  # Contains 1.24.0-1ubuntu1
            f.flush()
            probe.DPKG_STATUS_PATH = Path(f.name)

            result = await probe.run()

            assert result.success is True
            assert len(result.events) == 1

            event = result.events[0]
            assert event.category == "pkg"
            assert "nginx" in event.message
            assert "1.22.0-1ubuntu1" in event.message
            assert "1.24.0-1ubuntu1" in event.message
            assert event.raw["package_name"] == "nginx"
            assert event.raw["old_version"] == "1.22.0-1ubuntu1"
            assert event.raw["new_version"] == "1.24.0-1ubuntu1"

            Path(f.name).unlink()

    @pytest.mark.asyncio
    async def test_run_ignores_removed_package(self, probe, sample_dpkg_status):
        """Test run ignores packages not in installed state."""
        probe.set_watched_packages({"removed-package"})

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(sample_dpkg_status)
            f.flush()
            probe.DPKG_STATUS_PATH = Path(f.name)

            await probe.run()

            # Should not track removed package
            assert "removed-package" not in probe._package_versions

            Path(f.name).unlink()

    def test_is_installed(self, probe):
        """Test _is_installed method."""
        assert probe._is_installed("install ok installed") is True
        assert probe._is_installed("deinstall ok config-files") is False
        assert probe._is_installed(None) is False
        assert probe._is_installed("") is False

    def test_classify_upgrade(self, probe):
        """Test _classify_upgrade method."""
        # Major version change
        assert probe._classify_upgrade("1.0.0", "2.0.0") == "major"

        # Minor version change
        assert probe._classify_upgrade("1.0.0", "1.1.0") == "minor"

        # Patch version change
        assert probe._classify_upgrade("1.0.0", "1.0.1") == "patch"

        # Debian rebuild (same upstream)
        assert probe._classify_upgrade("1.0.0-1", "1.0.0-2") == "rebuild"

        # Invalid version
        assert probe._classify_upgrade("abc", "def") == "unknown"

    def test_extract_version_parts(self, probe):
        """Test _extract_version_parts method."""
        # Simple version
        assert probe._extract_version_parts("1.2.3") == (1, 2, 3)

        # Version with debian revision
        assert probe._extract_version_parts("1.24.0-1ubuntu1") == (1, 24, 0)

        # Version with epoch
        assert probe._extract_version_parts("1:2.3.4") == (2, 3, 4)

        # Version with epoch and debian revision
        assert probe._extract_version_parts("1:2.3.4-5ubuntu6") == (2, 3, 4)

        # Partial version
        assert probe._extract_version_parts("1.2") == (1, 2, 0)
        assert probe._extract_version_parts("1") == (1, 0, 0)

        # Invalid version
        assert probe._extract_version_parts("abc") is None

    def test_get_current_version(self, probe):
        """Test get_current_version method."""
        probe._package_versions = {"nginx": "1.24.0", "systemd": "255.4"}

        assert probe.get_current_version("nginx") == "1.24.0"
        assert probe.get_current_version("systemd") == "255.4"
        assert probe.get_current_version("unknown") is None

    def test_get_stats(self, probe):
        """Test get_stats method."""
        probe.set_watched_packages({"nginx", "systemd"})
        probe._package_versions = {"nginx": "1.24.0"}
        probe._run_count = 5
        probe._error_count = 1
        probe._initialized = True

        stats = probe.get_stats()

        assert stats["name"] == "package"
        assert stats["watched_packages"] == 2
        assert stats["tracked_packages"] == 1
        assert stats["run_count"] == 5
        assert stats["error_count"] == 1
        assert stats["initialized"] is True

    @pytest.mark.asyncio
    async def test_run_handles_missing_file(self, probe):
        """Test run handles missing dpkg status file."""
        probe.set_watched_packages({"nginx"})
        probe.DPKG_STATUS_PATH = Path("/nonexistent/path/status")

        result = await probe.run()

        assert result.success is True
        assert probe._initialized is True

    def test_create_upgrade_event(self, probe):
        """Test _create_upgrade_event method."""
        event = probe._create_upgrade_event("nginx", "1.22.0", "1.24.0")

        assert event.source == "probe"
        assert event.severity == "info"
        assert event.category == "pkg"
        assert event.entity == "package:nginx"
        assert "nginx" in event.message
        assert "1.22.0" in event.message
        assert "1.24.0" in event.message
        assert event.raw["package_name"] == "nginx"
        assert event.raw["old_version"] == "1.22.0"
        assert event.raw["new_version"] == "1.24.0"
        assert event.raw["upgrade_type"] == "minor"
        assert event.raw["event_type"] == "upgrade"


class TestPackageProbeNewPackageDetection:
    """Tests for new package detection functionality."""

    @pytest.fixture
    def probe_with_detection(self):
        """Create a PackageProbe with new package detection enabled."""
        return PackageProbe(detect_new_packages=True)

    def test_init_with_detection(self, probe_with_detection):
        """Test probe initialization with detection enabled."""
        assert probe_with_detection._detect_new_packages is True
        assert len(probe_with_detection._all_packages) == 0
        assert probe_with_detection._new_package_callback is None

    def test_enable_new_package_detection(self):
        """Test enabling new package detection."""
        probe = PackageProbe()
        assert probe._detect_new_packages is False

        probe.enable_new_package_detection(True)
        assert probe._detect_new_packages is True

        probe.enable_new_package_detection(False)
        assert probe._detect_new_packages is False

    def test_set_new_package_callback(self, probe_with_detection):
        """Test setting new package callback."""
        callback_called = []

        def callback(pkg_name: str, version: str) -> None:
            callback_called.append((pkg_name, version))

        probe_with_detection.set_new_package_callback(callback)
        assert probe_with_detection._new_package_callback is callback

    @pytest.mark.asyncio
    async def test_run_establishes_baseline_all_packages(self, probe_with_detection):
        """Test first run with detection enabled tracks all packages."""
        status_content = """Package: nginx
Status: install ok installed
Version: 1.24.0-1ubuntu1

Package: systemd
Status: install ok installed
Version: 255.4-1ubuntu8
"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(status_content)
            f.flush()
            probe_with_detection.DPKG_STATUS_PATH = Path(f.name)

            result = await probe_with_detection.run()

            assert result.success is True
            assert probe_with_detection._initialized is True
            assert "nginx" in probe_with_detection._all_packages
            assert "systemd" in probe_with_detection._all_packages
            assert len(result.events) == 0  # No events on first run

            Path(f.name).unlink()

    @pytest.mark.asyncio
    async def test_run_detects_new_package(self, probe_with_detection):
        """Test run detects newly installed package."""
        # Initialize with only nginx
        initial_status = """Package: nginx
Status: install ok installed
Version: 1.24.0-1ubuntu1
"""
        # Then add a new package
        updated_status = """Package: nginx
Status: install ok installed
Version: 1.24.0-1ubuntu1

Package: docker.io
Status: install ok installed
Version: 24.0.5-1ubuntu1
"""
        callback_called = []

        def callback(pkg_name: str, version: str) -> None:
            callback_called.append((pkg_name, version))

        probe_with_detection.set_new_package_callback(callback)

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            # First run - establish baseline
            f.write(initial_status)
            f.flush()
            probe_with_detection.DPKG_STATUS_PATH = Path(f.name)
            await probe_with_detection.run()

            # Second run - detect new package
            f.seek(0)
            f.truncate()
            f.write(updated_status)
            f.flush()

            result = await probe_with_detection.run()

            assert result.success is True
            assert len(result.events) == 1

            event = result.events[0]
            assert event.category == "pkg"
            assert "docker.io" in event.message
            assert event.raw["event_type"] == "install"
            assert event.raw["package_name"] == "docker.io"
            assert event.raw["version"] == "24.0.5-1ubuntu1"

            # Callback should have been called
            assert len(callback_called) == 1
            assert callback_called[0] == ("docker.io", "24.0.5-1ubuntu1")

            Path(f.name).unlink()

    def test_create_new_package_event(self, probe_with_detection):
        """Test _create_new_package_event method."""
        event = probe_with_detection._create_new_package_event("docker.io", "24.0.5-1ubuntu1")

        assert event.source == "probe"
        assert event.severity == "info"
        assert event.category == "pkg"
        assert event.entity == "package:docker.io"
        assert "docker.io" in event.message
        assert "24.0.5-1ubuntu1" in event.message
        assert event.raw["event_type"] == "install"
        assert event.raw["package_name"] == "docker.io"
        assert event.raw["version"] == "24.0.5-1ubuntu1"

    def test_get_stats_with_detection(self, probe_with_detection):
        """Test get_stats includes detection info."""
        probe_with_detection._all_packages = {
            "nginx": "1.24.0",
            "systemd": "255.4",
        }

        stats = probe_with_detection.get_stats()

        assert stats["all_packages"] == 2
        assert stats["detect_new_packages"] is True

    @pytest.mark.asyncio
    async def test_run_no_detection_does_not_track_all(self):
        """Test run without detection doesn't track all packages."""
        probe = PackageProbe(detect_new_packages=False)
        probe.set_watched_packages({"nginx"})

        status_content = """Package: nginx
Status: install ok installed
Version: 1.24.0-1ubuntu1

Package: systemd
Status: install ok installed
Version: 255.4-1ubuntu8
"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(status_content)
            f.flush()
            probe.DPKG_STATUS_PATH = Path(f.name)

            await probe.run()

            # Should only track watched package
            assert "nginx" in probe._package_versions
            # Should not track all packages
            assert len(probe._all_packages) == 0

            Path(f.name).unlink()
