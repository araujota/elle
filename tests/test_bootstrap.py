"""Tests for capability bootstrap system.

Tests automatic capability generation for core packages.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from elle.capabilities.autogen.bootstrap import (
    CORE_SYSTEM_PACKAGES,
    OPTIONAL_PACKAGES,
    BootstrapResult,
    BootstrapRunner,
    PackageCategory,
    get_bootstrap_packages,
    get_bootstrap_state,
    get_elle_dependencies,
    get_installed_optional_packages,
    is_package_installed,
    should_run_bootstrap,
)


class TestPackageCategory:
    """Tests for PackageCategory enum."""

    def test_categories_exist(self):
        """Test that all expected categories exist."""
        assert PackageCategory.SYSTEM.value == "system"
        assert PackageCategory.FILE.value == "file"
        assert PackageCategory.NETWORK.value == "network"
        assert PackageCategory.TEXT.value == "text"
        assert PackageCategory.PACKAGE.value == "package"
        assert PackageCategory.SERVICE.value == "service"
        assert PackageCategory.CONTAINER.value == "container"
        assert PackageCategory.MEDIA.value == "media"


class TestCorePackageLists:
    """Tests for core package lists."""

    def test_core_packages_not_empty(self):
        """Test that core packages list is populated."""
        assert len(CORE_SYSTEM_PACKAGES) > 10

    def test_optional_packages_not_empty(self):
        """Test that optional packages list is populated."""
        assert len(OPTIONAL_PACKAGES) > 5

    def test_core_packages_have_categories(self):
        """Test all core packages have valid categories."""
        for pkg_name, category in CORE_SYSTEM_PACKAGES:
            assert isinstance(pkg_name, str)
            assert isinstance(category, PackageCategory)

    def test_essential_packages_included(self):
        """Test that essential packages are in the list."""
        package_names = [p[0] for p in CORE_SYSTEM_PACKAGES]

        assert "coreutils" in package_names
        assert "systemd" in package_names
        assert "apt" in package_names
        assert "curl" in package_names


class TestBootstrapResult:
    """Tests for BootstrapResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = BootstrapResult()

        assert result.packages_attempted == 0
        assert result.packages_succeeded == 0
        assert result.packages_failed == 0
        assert result.capabilities_generated == 0
        assert result.completed_at is None

    def test_duration_calculation(self):
        """Test duration calculation."""
        result = BootstrapResult()
        result.started_at = datetime.utcnow()

        # Duration should be small but positive
        assert result.duration_seconds >= 0

    def test_success_rate_zero_attempted(self):
        """Test success rate when nothing attempted."""
        result = BootstrapResult()

        assert result.success_rate == 0.0

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        result = BootstrapResult()
        result.packages_attempted = 10
        result.packages_succeeded = 8

        assert result.success_rate == 80.0


class TestPackageDetection:
    """Tests for package detection functions."""

    @patch("subprocess.run")
    def test_is_package_installed_true(self, mock_run):
        """Test detecting installed package."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="install ok installed",
        )

        assert is_package_installed("coreutils") is True

    @patch("subprocess.run")
    def test_is_package_installed_false(self, mock_run):
        """Test detecting uninstalled package."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        assert is_package_installed("nonexistent-package") is False

    @patch("subprocess.run")
    def test_is_package_installed_timeout(self, mock_run):
        """Test handling timeout."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)

        assert is_package_installed("any-package") is False

    @patch("subprocess.run")
    def test_get_elle_dependencies(self, mock_run):
        """Test getting ELLE dependencies."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="python3, python3-gi, gir1.2-atspi-2.0",
        )

        deps = get_elle_dependencies()

        # Should include python packages even if elle package query fails
        assert isinstance(deps, list)


class TestBootstrapRunner:
    """Tests for BootstrapRunner class."""

    def test_default_options(self):
        """Test default runner options."""
        runner = BootstrapRunner()

        assert runner.include_core is True
        assert runner.include_optional is True
        assert runner.include_dependencies is True
        assert runner.max_concurrent == 3
        assert runner.skip_existing is True

    def test_custom_options(self):
        """Test custom runner options."""
        runner = BootstrapRunner(
            include_core=False,
            include_optional=False,
            include_dependencies=True,
            max_concurrent=5,
        )

        assert runner.include_core is False
        assert runner.include_optional is False
        assert runner.include_dependencies is True
        assert runner.max_concurrent == 5

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_get_packages_to_bootstrap(self, mock_installed):
        """Test getting packages to bootstrap."""
        # Mock some packages as installed
        mock_installed.side_effect = lambda p: p in ["coreutils", "apt", "curl"]

        runner = BootstrapRunner(
            include_core=True,
            include_optional=False,
            include_dependencies=False,
        )
        packages = runner.get_packages_to_bootstrap()

        # Should have some packages
        assert len(packages) > 0

        # All should be tuples
        for pkg_name, category in packages:
            assert isinstance(pkg_name, str)
            assert isinstance(category, PackageCategory)


class TestBootstrapState:
    """Tests for bootstrap state tracking."""

    def test_get_bootstrap_state_no_file(self):
        """Test getting state when file doesn't exist."""
        with patch("pathlib.Path.exists", return_value=False):
            state = get_bootstrap_state()

            assert state["completed"] is False
            assert state["last_run"] is None

    def test_should_run_bootstrap_first_time(self):
        """Test bootstrap should run on first time."""
        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": False},
        ):
            assert should_run_bootstrap() is True

    def test_should_run_bootstrap_version_changed(self):
        """Test bootstrap should run when version changes."""
        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": True, "version": "0.0.1"},
        ):
            # Current version is different
            assert should_run_bootstrap() is True

    def test_should_not_run_bootstrap_if_current(self):
        """Test bootstrap should not run if already complete and current."""
        from elle import __version__

        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": True, "version": __version__},
        ):
            assert should_run_bootstrap() is False


class TestGetBootstrapPackages:
    """Tests for get_bootstrap_packages convenience function."""

    @patch("elle.capabilities.autogen.bootstrap.is_package_installed")
    def test_returns_list_of_tuples(self, mock_installed):
        """Test that function returns list of tuples."""
        mock_installed.return_value = True

        packages = get_bootstrap_packages()

        assert isinstance(packages, list)
        if packages:  # May be empty if no packages detected
            pkg_name, category = packages[0]
            assert isinstance(pkg_name, str)
            assert isinstance(category, str)  # Category value as string


class TestLearnBootstrapCommand:
    """Tests for /learn bootstrap command."""

    @pytest.mark.asyncio
    async def test_bootstrap_dry_run(self):
        """Test bootstrap with --dry-run flag."""
        from elle.cli.package_learn_commands import _handle_bootstrap

        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_packages",
            return_value=[("coreutils", "system"), ("curl", "network")],
        ):
            result = await _handle_bootstrap("--dry-run")

            assert "Bootstrap would learn" in result
            assert "coreutils" in result
            assert "curl" in result

    @pytest.mark.asyncio
    async def test_bootstrap_status(self):
        """Test bootstrap status command."""
        from elle.cli.package_learn_commands import _handle_bootstrap_status

        with patch(
            "elle.capabilities.autogen.bootstrap.get_bootstrap_state",
            return_value={"completed": False},
        ):
            with patch(
                "elle.capabilities.autogen.bootstrap.get_bootstrap_packages",
                return_value=[("pkg1", "system")],
            ):
                result = await _handle_bootstrap_status("")

                assert "Bootstrap Status" in result
                assert "Not completed" in result
