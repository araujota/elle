"""Tests for package management capabilities."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pydantic
import pytest

from elle.capabilities.core.package import (
    PACKAGE_CAPABILITIES,
    ConflictDetail,
    PackageConflictDetectCapability,
    PackageConflictInput,
    PackageConflictOutput,
    PackageInfoCapability,
    PackageInfoOutput,
    PackageInput,
    PackageInstallCapability,
    PackageInstallOutput,
    PackageRemoveCapability,
    PackageRemoveOutput,
    PackageUpdateCapability,
    PackageUpdateOutput,
    PackageUpgradeInput,
    PackageUpgradeOutput,
    _get_broken_packages,
    _get_held_packages,
    _is_package_installed,
    _parse_apt_conflicts,
    _run_apt,
)

# Shared patch target prefixes
_PKG = "elle.capabilities.core.package"
# run_safe is imported locally inside helper functions, so patch at its origin
_RUN_SAFE = "elle.cli.subprocess_runner.run_safe"


def _make_subprocess_result(
    *,
    command: str = "test",
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    denied: bool = False,
) -> MagicMock:
    """Create a mock SubprocessResult with the given attributes."""
    result = MagicMock()
    result.command = command
    result.exit_code = exit_code
    result.stdout = stdout
    result.stderr = stderr
    result.timed_out = timed_out
    result.denied = denied
    result.success = exit_code == 0 and not timed_out and not denied
    result.failed = not result.success
    return result


# =============================================================================
# Pydantic Model Validation
# =============================================================================


class TestPackageInputValidation:
    """Tests for Pydantic validation of PackageInput model."""

    def test_valid_input(self):
        """Valid package name and default timeout accepted."""
        inp = PackageInput(package="nginx")
        assert inp.package == "nginx"
        assert inp.timeout_sec == 300

    def test_custom_timeout(self):
        """Custom timeout within bounds accepted."""
        inp = PackageInput(package="curl", timeout_sec=600)
        assert inp.timeout_sec == 600

    def test_empty_package_name_rejected(self):
        """Empty package name fails min_length=1 validation."""
        with pytest.raises(pydantic.ValidationError):
            PackageInput(package="")

    def test_timeout_below_minimum_rejected(self):
        """Timeout below 30 seconds rejected by ge=30 constraint."""
        with pytest.raises(pydantic.ValidationError):
            PackageInput(package="nginx", timeout_sec=10)

    def test_timeout_above_maximum_rejected(self):
        """Timeout above 3600 seconds rejected by le=3600 constraint."""
        with pytest.raises(pydantic.ValidationError):
            PackageInput(package="nginx", timeout_sec=5000)

    def test_frozen_model(self):
        """PackageInput is frozen; attribute assignment raises error."""
        inp = PackageInput(package="nginx")
        with pytest.raises(pydantic.ValidationError):
            inp.package = "curl"


class TestPackageUpgradeInputValidation:
    """Tests for Pydantic validation of PackageUpgradeInput model."""

    def test_defaults(self):
        """Default values are None for package and 600 for timeout."""
        inp = PackageUpgradeInput()
        assert inp.package is None
        assert inp.timeout_sec == 600

    def test_specific_package(self):
        """Specific package name accepted."""
        inp = PackageUpgradeInput(package="nginx")
        assert inp.package == "nginx"

    def test_upgrade_timeout_below_minimum_rejected(self):
        """Upgrade timeout below 60 seconds rejected."""
        with pytest.raises(pydantic.ValidationError):
            PackageUpgradeInput(timeout_sec=30)

    def test_upgrade_timeout_above_maximum_rejected(self):
        """Upgrade timeout above 7200 seconds rejected."""
        with pytest.raises(pydantic.ValidationError):
            PackageUpgradeInput(timeout_sec=10000)


class TestPackageConflictInputValidation:
    """Tests for Pydantic validation of PackageConflictInput model."""

    def test_valid_input(self):
        """Valid conflict input accepted with defaults."""
        inp = PackageConflictInput(package="nginx")
        assert inp.package == "nginx"
        assert inp.include_recommendations is True
        assert inp.timeout_sec == 60

    def test_empty_package_rejected(self):
        """Empty package name rejected."""
        with pytest.raises(pydantic.ValidationError):
            PackageConflictInput(package="")

    def test_conflict_timeout_below_minimum_rejected(self):
        """Conflict timeout below 10 rejected."""
        with pytest.raises(pydantic.ValidationError):
            PackageConflictInput(package="x", timeout_sec=5)

    def test_conflict_timeout_above_maximum_rejected(self):
        """Conflict timeout above 300 rejected."""
        with pytest.raises(pydantic.ValidationError):
            PackageConflictInput(package="x", timeout_sec=400)


# =============================================================================
# Helper Functions
# =============================================================================


class TestRunApt:
    """Tests for the _run_apt helper function."""

    @patch(_RUN_SAFE)
    def test_run_apt_success(self, mock_run_safe):
        """Successful apt command returns (True, stdout, stderr, 0)."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="Reading package lists...",
            stderr="",
            exit_code=0,
        )

        success, stdout, stderr, code = _run_apt("update")

        assert success is True
        assert "Reading package lists" in stdout
        assert code == 0
        call_args = mock_run_safe.call_args
        assert "DEBIAN_FRONTEND=noninteractive" in call_args[0][0]
        assert "apt-get update" in call_args[0][0]

    @patch(_RUN_SAFE)
    def test_run_apt_failure(self, mock_run_safe):
        """Failed apt command returns (False, ..., exit_code)."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="",
            stderr="E: Unable to locate package",
            exit_code=100,
        )

        success, stdout, stderr, code = _run_apt("install -y nonexistent")

        assert success is False
        assert "Unable to locate package" in stderr
        assert code == 100


class TestIsPackageInstalled:
    """Tests for the _is_package_installed helper function."""

    @patch(_RUN_SAFE)
    def test_installed_package(self, mock_run_safe):
        """Installed package returns (True, version)."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="install ok installed 1.18.0-6ubuntu14.4",
        )

        installed, version = _is_package_installed("nginx")

        assert installed is True
        assert version == "1.18.0-6ubuntu14.4"

    @patch(_RUN_SAFE)
    def test_not_installed_package(self, mock_run_safe):
        """Not-installed package returns (False, '')."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="",
            exit_code=1,
        )

        installed, version = _is_package_installed("nonexistent")

        assert installed is False
        assert version == ""

    @patch(_RUN_SAFE)
    def test_deinstall_status(self, mock_run_safe):
        """Package in deinstall state returns (False, '')."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="deinstall ok config-files 1.0.0",
        )

        installed, version = _is_package_installed("removed-pkg")

        assert installed is False
        assert version == ""


class TestParseAptConflicts:
    """Tests for the _parse_apt_conflicts helper function."""

    def test_depends_conflict(self):
        """Parses a Depends: conflict line."""
        stderr = "Depends: libfoo (>= 2.0) but 1.5 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert len(conflicts) == 1
        assert conflicts[0].conflicting_package == "libfoo"
        assert conflicts[0].conflict_type == "depends"
        assert conflicts[0].required_version == ">= 2.0"
        assert conflicts[0].installed_version == "1.5"

    def test_breaks_conflict(self):
        """Parses a Breaks: conflict line."""
        stderr = "Breaks: libbar (<< 3.0) but 2.5 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert len(conflicts) == 1
        assert conflicts[0].conflicting_package == "libbar"
        assert conflicts[0].conflict_type == "breaks"

    def test_conflicts_type(self):
        """Parses a Conflicts: conflict line."""
        stderr = "Conflicts: libbaz (>= 1.0) but 0.9 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert len(conflicts) == 1
        assert conflicts[0].conflicting_package == "libbaz"
        assert conflicts[0].conflict_type == "conflicts"

    def test_no_conflicts_in_output(self):
        """Returns empty list when no conflict patterns match."""
        stderr = "Some random error that is not a conflict"
        conflicts = _parse_apt_conflicts(stderr)

        assert conflicts == []

    def test_empty_string(self):
        """Returns empty list for empty string."""
        conflicts = _parse_apt_conflicts("")
        assert len(conflicts) == 0

    def test_multiple_conflicts(self):
        """Parses multiple conflict lines."""
        stderr = "Depends: libfoo (>= 2.0) but 1.5 is installed\nBreaks: libbar (<< 3.0) but 2.5 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert len(conflicts) == 2
        assert conflicts[0].conflict_type == "depends"
        assert conflicts[1].conflict_type == "breaks"

    def test_depends_resolution_text(self):
        """Depends conflict resolution text contains package name and version."""
        stderr = "Depends: libfoo (>= 2.0) but 1.5 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert "libfoo" in conflicts[0].resolution
        assert ">= 2.0" in conflicts[0].resolution

    def test_breaks_resolution_text(self):
        """Breaks conflict resolution text contains package name."""
        stderr = "Breaks: libbar (<< 3.0) but 2.5 is installed"
        conflicts = _parse_apt_conflicts(stderr)

        assert "libbar" in conflicts[0].resolution


class TestGetHeldPackages:
    """Tests for the _get_held_packages helper function."""

    @patch(_RUN_SAFE)
    def test_held_packages_found(self, mock_run_safe):
        """Returns list of held package names."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="linux-image-generic\nnginx\n",
        )

        held = _get_held_packages()

        assert held == ["linux-image-generic", "nginx"]

    @patch(_RUN_SAFE)
    def test_no_held_packages(self, mock_run_safe):
        """Returns empty list when no packages are held."""
        mock_run_safe.return_value = _make_subprocess_result(stdout="")

        held = _get_held_packages()

        assert held == []

    @patch(_RUN_SAFE)
    def test_command_failure(self, mock_run_safe):
        """Returns empty list when apt-mark command fails."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="",
            exit_code=1,
        )

        held = _get_held_packages()

        assert held == []


class TestGetBrokenPackages:
    """Tests for the _get_broken_packages helper function."""

    @patch(_RUN_SAFE)
    def test_broken_packages_found(self, mock_run_safe):
        """Returns list of broken package descriptions."""
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="libfoo 1.0 has broken deps\nlibbar needs upgrade\n",
        )

        broken = _get_broken_packages()

        assert len(broken) == 2

    @patch(_RUN_SAFE)
    def test_no_broken_packages(self, mock_run_safe):
        """Returns empty list when dpkg --audit is clean."""
        mock_run_safe.return_value = _make_subprocess_result(stdout="")

        broken = _get_broken_packages()

        assert broken == []


# =============================================================================
# PackageInstallCapability
# =============================================================================


class TestPackageInstallSpec:
    """Tests for PackageInstallCapability spec metadata."""

    def setup_method(self):
        self.cap = PackageInstallCapability()

    def test_spec_name(self):
        """Spec name is 'package.install'."""
        assert self.cap.spec.name == "package.install"

    def test_spec_domain(self):
        """Spec domain is 'package'."""
        assert self.cap.spec.domain == "package"

    def test_spec_risk(self):
        """Spec risk is 'medium'."""
        assert self.cap.spec.risk == "medium"

    def test_spec_requires_privilege(self):
        """Spec requires privilege."""
        assert self.cap.spec.requires_privilege is True

    def test_spec_idempotent(self):
        """Spec is idempotent."""
        assert self.cap.spec.idempotent is True

    def test_spec_side_effects(self):
        """Spec has a package_change side effect."""
        assert len(self.cap.spec.side_effects) == 1
        assert self.cap.spec.side_effects[0].kind == "package_change"


class TestPackageInstallRun:
    """Tests for PackageInstallCapability run method."""

    def setup_method(self):
        self.cap = PackageInstallCapability()

    @patch(f"{_PKG}._is_package_installed")
    def test_already_installed(self, mock_installed):
        """Package already installed returns success with already_installed=True."""
        mock_installed.return_value = (True, "1.18.0")

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.already_installed is True
        assert result.output.version == "1.18.0"
        assert result.output.package == "nginx"
        assert "already installed" in result.evidence.rationale

    @patch(f"{_PKG}._is_package_installed")
    @patch(f"{_PKG}._run_apt")
    def test_install_success(self, mock_run_apt, mock_installed):
        """Successful install returns success with version."""
        mock_installed.side_effect = [
            (False, ""),
            (True, "1.18.0"),
        ]
        mock_run_apt.return_value = (True, "Installing nginx...", "", 0)

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.already_installed is False
        assert result.output.version == "1.18.0"
        assert result.output.package == "nginx"
        assert len(result.side_effects_applied) == 1
        assert "apt-get install -y nginx" in result.evidence.commands_executed

    @patch(f"{_PKG}._is_package_installed")
    @patch(f"{_PKG}._run_apt")
    def test_install_failure(self, mock_run_apt, mock_installed):
        """Failed install returns success=False with error."""
        mock_installed.return_value = (False, "")
        mock_run_apt.return_value = (False, "", "E: Unable to locate package foo", 100)

        inp = PackageInput(package="foo")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Unable to locate package" in result.error
        assert result.execution_time_ms >= 0

    @patch(f"{_PKG}._is_package_installed")
    @patch(f"{_PKG}._run_apt")
    def test_install_failure_empty_stderr(self, mock_run_apt, mock_installed):
        """Failed install with empty stderr uses fallback error message."""
        mock_installed.return_value = (False, "")
        mock_run_apt.return_value = (False, "", "", 1)

        inp = PackageInput(package="bad-pkg")
        result = self.cap.run(inp)

        assert result.success is False
        assert "exit code 1" in result.error


class TestPackageInstallDryRun:
    """Tests for PackageInstallCapability dry_run method."""

    def setup_method(self):
        self.cap = PackageInstallCapability()

    @patch(f"{_PKG}._is_package_installed")
    def test_dry_run_already_installed(self, mock_installed):
        """Dry run for already-installed package returns no-op preview."""
        mock_installed.return_value = (True, "1.18.0")

        inp = PackageInput(package="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"
        assert "already installed" in result.preview_text

    @patch(f"{_PKG}._run_apt")
    @patch(f"{_PKG}._is_package_installed")
    def test_dry_run_new_package(self, mock_installed, mock_run_apt):
        """Dry run for new package shows what would be installed."""
        mock_installed.return_value = (False, "")
        mock_run_apt.return_value = (True, "Inst nginx (1.18.0)", "", 0)

        inp = PackageInput(package="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert result.estimated_risk == "medium"
        assert "Would install nginx" in result.preview_text
        assert result.diff == "Inst nginx (1.18.0)"

    @patch(f"{_PKG}._run_apt")
    @patch(f"{_PKG}._is_package_installed")
    def test_dry_run_package_not_found(self, mock_installed, mock_run_apt):
        """Dry run for unavailable package returns invalid result."""
        mock_installed.return_value = (False, "")
        mock_run_apt.return_value = (False, "", "E: Unable to locate package foo", 100)

        inp = PackageInput(package="foo")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "Unable to locate package" in result.validation_errors[0]

    @patch(f"{_PKG}._run_apt")
    @patch(f"{_PKG}._is_package_installed")
    def test_dry_run_failure_empty_stderr(self, mock_installed, mock_run_apt):
        """Dry run failure with empty stderr uses fallback 'Package not found' text."""
        mock_installed.return_value = (False, "")
        mock_run_apt.return_value = (False, "", "", 100)

        inp = PackageInput(package="foo")
        result = self.cap.dry_run(inp)

        assert result.is_valid is False
        assert "Package not found" in result.validation_errors[0]


class TestPackageInstallVerify:
    """Tests for PackageInstallCapability verify method."""

    def setup_method(self):
        self.cap = PackageInstallCapability()

    @patch(f"{_PKG}._is_package_installed")
    def test_verify_installed(self, mock_installed):
        """Verify passes when package is installed."""
        mock_installed.return_value = (True, "1.18.0")

        inp = PackageInput(package="nginx")
        result = self.cap.verify(inp)

        assert result.passed is True
        assert result.actual_state["installed"] is True
        assert result.actual_state["version"] == "1.18.0"
        assert result.expected_state == {"installed": True}
        assert result.discrepancies == ()

    @patch(f"{_PKG}._is_package_installed")
    def test_verify_not_installed(self, mock_installed):
        """Verify fails when package is not installed."""
        mock_installed.return_value = (False, "")

        inp = PackageInput(package="nginx")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "not installed" in result.discrepancies[0]


class TestPackageInstallRollback:
    """Tests for PackageInstallCapability rollback method."""

    def setup_method(self):
        self.cap = PackageInstallCapability()

    def test_rollback_skipped_already_installed(self):
        """Rollback skipped when package was already installed."""
        mock_result = MagicMock()
        mock_result.output = PackageInstallOutput(
            package="nginx",
            version="1.18.0",
            already_installed=True,
        )

        inp = PackageInput(package="nginx")
        rollback = self.cap.rollback(inp, mock_result)

        assert rollback.success is True
        assert "no rollback needed" in rollback.error

    @patch(f"{_PKG}._run_apt")
    def test_rollback_success(self, mock_run_apt):
        """Successful rollback removes the package."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_result = MagicMock()
        mock_result.output = PackageInstallOutput(
            package="nginx",
            version="1.18.0",
            already_installed=False,
        )

        inp = PackageInput(package="nginx")
        rollback = self.cap.rollback(inp, mock_result)

        assert rollback.success is True
        assert "package:nginx" in rollback.rolled_back
        assert rollback.failed_to_rollback == ()
        assert "apt-get remove -y nginx" in rollback.commands_executed

    @patch(f"{_PKG}._run_apt")
    def test_rollback_failure(self, mock_run_apt):
        """Failed rollback returns error."""
        mock_run_apt.return_value = (False, "", "E: removal failed", 1)
        mock_result = MagicMock()
        mock_result.output = PackageInstallOutput(
            package="nginx",
            version="1.18.0",
            already_installed=False,
        )

        inp = PackageInput(package="nginx")
        rollback = self.cap.rollback(inp, mock_result)

        assert rollback.success is False
        assert "removal failed" in rollback.error
        assert "package:nginx" in rollback.failed_to_rollback


# =============================================================================
# PackageRemoveCapability
# =============================================================================


class TestPackageRemoveSpec:
    """Tests for PackageRemoveCapability spec metadata."""

    def setup_method(self):
        self.cap = PackageRemoveCapability()

    def test_spec_name(self):
        """Spec name is 'package.remove'."""
        assert self.cap.spec.name == "package.remove"

    def test_spec_risk(self):
        """Spec risk is 'high'."""
        assert self.cap.spec.risk == "high"

    def test_spec_requires_privilege(self):
        """Spec requires privilege."""
        assert self.cap.spec.requires_privilege is True


class TestPackageRemoveRun:
    """Tests for PackageRemoveCapability run method."""

    def setup_method(self):
        self.cap = PackageRemoveCapability()

    @patch(f"{_PKG}._is_package_installed")
    def test_not_installed(self, mock_installed):
        """Removing a not-installed package returns success with was_installed=False."""
        mock_installed.return_value = (False, "")

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.was_installed is False
        assert "not installed" in result.evidence.rationale

    @patch(f"{_PKG}._run_apt")
    @patch(f"{_PKG}._is_package_installed")
    def test_remove_success(self, mock_installed, mock_run_apt):
        """Successful removal returns success with was_installed=True."""
        mock_installed.return_value = (True, "1.18.0")
        mock_run_apt.return_value = (True, "Removing nginx...", "", 0)

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.was_installed is True
        assert len(result.side_effects_applied) == 1
        assert "apt-get remove -y nginx" in result.evidence.commands_executed

    @patch(f"{_PKG}._run_apt")
    @patch(f"{_PKG}._is_package_installed")
    def test_remove_failure(self, mock_installed, mock_run_apt):
        """Failed removal returns error."""
        mock_installed.return_value = (True, "1.18.0")
        mock_run_apt.return_value = (False, "", "E: remove failed", 1)

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is False
        assert "remove failed" in result.error

    @patch(f"{_PKG}._run_apt")
    @patch(f"{_PKG}._is_package_installed")
    def test_remove_failure_empty_stderr(self, mock_installed, mock_run_apt):
        """Failed removal with empty stderr uses fallback error message."""
        mock_installed.return_value = (True, "1.18.0")
        mock_run_apt.return_value = (False, "", "", 2)

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is False
        assert "exit code 2" in result.error


class TestPackageRemoveDryRun:
    """Tests for PackageRemoveCapability dry_run method."""

    def setup_method(self):
        self.cap = PackageRemoveCapability()

    @patch(f"{_PKG}._is_package_installed")
    def test_dry_run_not_installed(self, mock_installed):
        """Dry run for not-installed package shows nothing to do."""
        mock_installed.return_value = (False, "")

        inp = PackageInput(package="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"
        assert "not installed" in result.preview_text

    @patch(f"{_PKG}._run_apt")
    @patch(f"{_PKG}._is_package_installed")
    def test_dry_run_installed(self, mock_installed, mock_run_apt):
        """Dry run for installed package shows removal preview."""
        mock_installed.return_value = (True, "1.18.0")
        mock_run_apt.return_value = (True, "Remv nginx 1.18.0", "", 0)

        inp = PackageInput(package="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert result.estimated_risk == "high"
        assert "Would remove nginx" in result.preview_text
        assert result.diff == "Remv nginx 1.18.0"


class TestPackageRemoveVerify:
    """Tests for PackageRemoveCapability verify method."""

    def setup_method(self):
        self.cap = PackageRemoveCapability()

    @patch(f"{_PKG}._is_package_installed")
    def test_verify_removed(self, mock_installed):
        """Verify passes when package is not installed."""
        mock_installed.return_value = (False, "")

        inp = PackageInput(package="nginx")
        result = self.cap.verify(inp)

        assert result.passed is True
        assert result.expected_state == {"installed": False}
        assert result.discrepancies == ()

    @patch(f"{_PKG}._is_package_installed")
    def test_verify_still_installed(self, mock_installed):
        """Verify fails when package is still installed."""
        mock_installed.return_value = (True, "1.18.0")

        inp = PackageInput(package="nginx")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert "still installed" in result.discrepancies[0]


class TestPackageRemoveRollback:
    """Tests for PackageRemoveCapability rollback method."""

    def setup_method(self):
        self.cap = PackageRemoveCapability()

    def test_rollback_skipped_not_installed(self):
        """Rollback skipped when package was not previously installed."""
        mock_result = MagicMock()
        mock_result.output = PackageRemoveOutput(
            package="nginx",
            was_installed=False,
        )

        inp = PackageInput(package="nginx")
        rollback = self.cap.rollback(inp, mock_result)

        assert rollback.success is True
        assert "no rollback needed" in rollback.error

    @patch(f"{_PKG}._run_apt")
    def test_rollback_reinstall_success(self, mock_run_apt):
        """Successful rollback reinstalls the package."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_result = MagicMock()
        mock_result.output = PackageRemoveOutput(
            package="nginx",
            was_installed=True,
        )

        inp = PackageInput(package="nginx")
        rollback = self.cap.rollback(inp, mock_result)

        assert rollback.success is True
        assert "package:nginx" in rollback.rolled_back
        assert "apt-get install -y nginx" in rollback.commands_executed

    @patch(f"{_PKG}._run_apt")
    def test_rollback_reinstall_failure(self, mock_run_apt):
        """Failed rollback returns error."""
        mock_run_apt.return_value = (False, "", "E: reinstall failed", 1)
        mock_result = MagicMock()
        mock_result.output = PackageRemoveOutput(
            package="nginx",
            was_installed=True,
        )

        inp = PackageInput(package="nginx")
        rollback = self.cap.rollback(inp, mock_result)

        assert rollback.success is False
        assert "reinstall failed" in rollback.error
        assert "package:nginx" in rollback.failed_to_rollback


# =============================================================================
# PackageUpdateCapability
# =============================================================================


class TestPackageUpdateSpec:
    """Tests for PackageUpdateCapability spec metadata."""

    def setup_method(self):
        self.cap = PackageUpdateCapability()

    def test_spec_name(self):
        """Spec name is 'package.update'."""
        assert self.cap.spec.name == "package.update"

    def test_spec_risk(self):
        """Spec risk is 'low'."""
        assert self.cap.spec.risk == "low"

    def test_spec_no_side_effects(self):
        """Update has no declared side effects (metadata only)."""
        assert self.cap.spec.side_effects == ()


class TestPackageUpdateRun:
    """Tests for PackageUpdateCapability run method."""

    def setup_method(self):
        self.cap = PackageUpdateCapability()

    @patch(f"{_PKG}._run_apt")
    def test_update_success_counts_sources(self, mock_run_apt):
        """Successful update counts Hit: and Get: lines."""
        mock_run_apt.return_value = (
            True,
            "Hit:1 http://archive.ubuntu.com\nGet:2 http://ppa.launchpad.net\nHit:3 http://security.ubuntu.com\n",
            "",
            0,
        )

        inp = PackageInput(package="dummy")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.packages_updated == 3
        assert "apt-get update" in result.evidence.commands_executed

    @patch(f"{_PKG}._run_apt")
    def test_update_failure(self, mock_run_apt):
        """Failed update returns error."""
        mock_run_apt.return_value = (False, "", "E: update failed", 1)

        inp = PackageInput(package="dummy")
        result = self.cap.run(inp)

        assert result.success is False
        assert "update failed" in result.error

    @patch(f"{_PKG}._run_apt")
    def test_update_failure_empty_stderr(self, mock_run_apt):
        """Failed update with empty stderr uses fallback error message."""
        mock_run_apt.return_value = (False, "", "", 1)

        inp = PackageInput(package="dummy")
        result = self.cap.run(inp)

        assert result.success is False
        assert "exit code 1" in result.error

    @patch(f"{_PKG}._run_apt")
    def test_update_zero_sources(self, mock_run_apt):
        """Update with no Hit: or Get: lines returns 0 packages_updated."""
        mock_run_apt.return_value = (True, "All packages are up to date.\n", "", 0)

        inp = PackageInput(package="dummy")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.packages_updated == 0


class TestPackageUpdateDryRun:
    """Tests for PackageUpdateCapability dry_run method."""

    def setup_method(self):
        self.cap = PackageUpdateCapability()

    def test_dry_run_preview(self):
        """Dry run always returns a valid preview."""
        inp = PackageInput(package="dummy")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "low"
        assert "update package lists" in result.preview_text.lower()
        assert "apt-get update" in result.would_execute


# =============================================================================
# PackageInfoCapability
# =============================================================================


class TestPackageInfoSpec:
    """Tests for PackageInfoCapability spec metadata."""

    def setup_method(self):
        self.cap = PackageInfoCapability()

    def test_spec_name(self):
        """Spec name is 'package.info'."""
        assert self.cap.spec.name == "package.info"

    def test_spec_risk_none(self):
        """Spec risk is 'none'."""
        assert self.cap.spec.risk == "none"

    def test_spec_no_privilege(self):
        """Info does not require privilege."""
        assert self.cap.spec.requires_privilege is False


class TestPackageInfoRun:
    """Tests for PackageInfoCapability run method."""

    def setup_method(self):
        self.cap = PackageInfoCapability()

    @patch(_RUN_SAFE)
    @patch(f"{_PKG}._is_package_installed")
    def test_info_installed_package(self, mock_installed, mock_run_safe):
        """Info for installed package returns installed version and description."""
        mock_installed.return_value = (True, "1.18.0")
        mock_run_safe.return_value = _make_subprocess_result(
            stdout=(
                "Package: nginx\n"
                "Version: 1.18.0-6ubuntu14.4\n"
                "Description: small, powerful, scalable web server\n"
                "Size: 893408\n"
            ),
        )

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.installed is True
        assert result.output.version == "1.18.0"
        assert result.output.description == "small, powerful, scalable web server"
        assert result.output.size == "893408"
        assert result.output.package == "nginx"

    @patch(_RUN_SAFE)
    @patch(f"{_PKG}._is_package_installed")
    def test_info_not_installed_uses_cache_version(self, mock_installed, mock_run_safe):
        """Info for non-installed package uses version from apt-cache."""
        mock_installed.return_value = (False, "")
        mock_run_safe.return_value = _make_subprocess_result(
            stdout=("Package: nginx\nVersion: 1.18.0-6ubuntu14.4\nDescription: small, powerful, scalable web server\n"),
        )

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.installed is False
        assert result.output.version == "1.18.0-6ubuntu14.4"

    @patch(_RUN_SAFE)
    @patch(f"{_PKG}._is_package_installed")
    def test_info_no_cache_data(self, mock_installed, mock_run_safe):
        """Info when apt-cache has no data returns empty fields."""
        mock_installed.return_value = (False, "")
        mock_run_safe.return_value = _make_subprocess_result(
            stdout="",
            exit_code=100,
        )

        inp = PackageInput(package="nonexistent")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.installed is False
        assert result.output.version == ""
        assert result.output.description == ""
        assert result.output.size == ""

    @patch(_RUN_SAFE)
    @patch(f"{_PKG}._is_package_installed")
    def test_info_multiple_versions_uses_first(self, mock_installed, mock_run_safe):
        """When apt-cache returns multiple versions, uses the first one."""
        mock_installed.return_value = (False, "")
        mock_run_safe.return_value = _make_subprocess_result(
            stdout=(
                "Package: nginx\n"
                "Version: 1.18.0-6ubuntu14.4\n"
                "Description: web server\n"
                "\n"
                "Package: nginx\n"
                "Version: 1.14.0-0ubuntu1\n"
                "Description: old web server\n"
            ),
        )

        inp = PackageInput(package="nginx")
        result = self.cap.run(inp)

        assert result.output.version == "1.18.0-6ubuntu14.4"

    @patch(_RUN_SAFE)
    @patch(f"{_PKG}._is_package_installed")
    def test_info_evidence_records_command(self, mock_installed, mock_run_safe):
        """Evidence records the apt-cache show command."""
        mock_installed.return_value = (False, "")
        mock_run_safe.return_value = _make_subprocess_result(stdout="")

        inp = PackageInput(package="curl")
        result = self.cap.run(inp)

        assert "apt-cache show curl" in result.evidence.commands_executed


class TestPackageInfoDryRun:
    """Tests for PackageInfoCapability dry_run method."""

    def setup_method(self):
        self.cap = PackageInfoCapability()

    def test_dry_run_preview(self):
        """Dry run shows apt-cache command that would run."""
        inp = PackageInput(package="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"
        assert "apt-cache show nginx" in result.would_execute[0]


# =============================================================================
# PackageConflictDetectCapability
# =============================================================================


class TestPackageConflictSpec:
    """Tests for PackageConflictDetectCapability spec metadata."""

    def setup_method(self):
        self.cap = PackageConflictDetectCapability()

    def test_spec_name(self):
        """Spec name is 'package.detect-conflicts'."""
        assert self.cap.spec.name == "package.detect-conflicts"

    def test_spec_risk_none(self):
        """Spec risk is 'none' (read-only)."""
        assert self.cap.spec.risk == "none"

    def test_spec_no_side_effects(self):
        """Conflict detection has no side effects."""
        assert self.cap.spec.side_effects == ()

    def test_spec_idempotent(self):
        """Conflict detection is idempotent."""
        assert self.cap.spec.idempotent is True

    def test_spec_no_privilege(self):
        """Conflict detection does not require privilege."""
        assert self.cap.spec.requires_privilege is False


class TestPackageConflictRun:
    """Tests for PackageConflictDetectCapability run method."""

    def setup_method(self):
        self.cap = PackageConflictDetectCapability()

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_no_conflicts(self, mock_run_apt, mock_held, mock_broken):
        """No conflicts returns has_conflicts=False."""
        mock_run_apt.return_value = (True, "nginx is already installed", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = []

        inp = PackageConflictInput(package="nginx")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.has_conflicts is False
        assert result.output.conflicts == ()
        assert result.output.broken_deps == ()

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_dependency_conflict_detected(self, mock_run_apt, mock_held, mock_broken):
        """Dependency conflict parsed from dry-run stderr."""
        mock_run_apt.return_value = (
            False,
            "",
            "Depends: libfoo (>= 2.0) but 1.5 is installed",
            100,
        )
        mock_held.return_value = []
        mock_broken.return_value = []

        inp = PackageConflictInput(package="bad-pkg")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.has_conflicts is True
        assert len(result.output.conflicts) == 1
        assert result.output.conflicts[0].conflict_type == "depends"
        assert result.output.conflicts[0].conflicting_package == "libfoo"

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_unable_to_locate_recommendation(self, mock_run_apt, mock_held, mock_broken):
        """'Unable to locate package' generates an apt update recommendation."""
        mock_run_apt.return_value = (
            False,
            "",
            "E: Unable to locate package nonexistent",
            100,
        )
        mock_held.return_value = []
        mock_broken.return_value = []

        inp = PackageConflictInput(package="nonexistent")
        result = self.cap.run(inp)

        assert result.success is True
        assert any("apt update" in r for r in result.output.recommendations)

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_held_package_detected(self, mock_run_apt, mock_held, mock_broken):
        """Held target package creates a held conflict entry."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_held.return_value = ["nginx"]
        mock_broken.return_value = []

        inp = PackageConflictInput(package="nginx")
        result = self.cap.run(inp)

        assert result.output.has_conflicts is True
        assert len(result.output.conflicts) == 1
        assert result.output.conflicts[0].conflict_type == "held"
        assert "unhold" in result.output.conflicts[0].resolution

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_held_packages_in_recommendations(self, mock_run_apt, mock_held, mock_broken):
        """Held packages generate a recommendation when include_recommendations is True."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_held.return_value = ["linux-image-generic"]
        mock_broken.return_value = []

        inp = PackageConflictInput(package="curl", include_recommendations=True)
        result = self.cap.run(inp)

        assert any("Held packages" in r for r in result.output.recommendations)

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_held_packages_no_recommendations_when_disabled(self, mock_run_apt, mock_held, mock_broken):
        """Held packages do NOT generate recommendation when include_recommendations is False."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_held.return_value = ["linux-image-generic"]
        mock_broken.return_value = []

        inp = PackageConflictInput(package="curl", include_recommendations=False)
        result = self.cap.run(inp)

        assert not any("Held packages" in r for r in result.output.recommendations)

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_broken_deps_detected(self, mock_run_apt, mock_held, mock_broken):
        """Broken packages trigger has_conflicts and recommendations."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = ["libfoo has broken deps"]

        inp = PackageConflictInput(package="nginx")
        result = self.cap.run(inp)

        assert result.output.has_conflicts is True
        assert len(result.output.broken_deps) == 1
        assert any("dpkg --configure" in r for r in result.output.recommendations)
        assert any("apt-get install -f" in r for r in result.output.recommendations)

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_broken_deps_no_recommendations_when_disabled(self, mock_run_apt, mock_held, mock_broken):
        """Broken deps with include_recommendations=False do not generate recommendations."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = ["libfoo has broken deps"]

        inp = PackageConflictInput(package="nginx", include_recommendations=False)
        result = self.cap.run(inp)

        assert result.output.has_conflicts is True
        assert result.output.recommendations == ()

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_evidence_commands_recorded(self, mock_run_apt, mock_held, mock_broken):
        """All commands executed are recorded in evidence."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = []

        inp = PackageConflictInput(package="nginx")
        result = self.cap.run(inp)

        cmds = result.evidence.commands_executed
        assert any("dry-run" in c for c in cmds)
        assert any("showhold" in c for c in cmds)
        assert any("--audit" in c for c in cmds)

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_evidence_man_citations(self, mock_run_apt, mock_held, mock_broken):
        """Evidence includes relevant man page citations."""
        mock_run_apt.return_value = (True, "", "", 0)
        mock_held.return_value = []
        mock_broken.return_value = []

        inp = PackageConflictInput(package="test")
        result = self.cap.run(inp)

        assert "apt-get(8)" in result.evidence.man_vault_citations
        assert "dpkg(1)" in result.evidence.man_vault_citations
        assert "apt-cache(8)" in result.evidence.man_vault_citations

    @patch(f"{_PKG}._get_broken_packages")
    @patch(f"{_PKG}._get_held_packages")
    @patch(f"{_PKG}._run_apt")
    def test_breaks_conflict_detected(self, mock_run_apt, mock_held, mock_broken):
        """Breaks conflict parsed from dry-run stderr."""
        mock_run_apt.return_value = (
            False,
            "",
            "Breaks: libbar (<< 3.0) but 2.5 is installed",
            1,
        )
        mock_held.return_value = []
        mock_broken.return_value = []

        inp = PackageConflictInput(package="test-pkg")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.has_conflicts is True
        assert result.output.conflicts[0].conflicting_package == "libbar"


class TestPackageConflictDryRun:
    """Tests for PackageConflictDetectCapability dry_run method."""

    def setup_method(self):
        self.cap = PackageConflictDetectCapability()

    def test_dry_run_preview(self):
        """Dry run lists all commands that would execute."""
        inp = PackageConflictInput(package="nginx")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert result.estimated_risk == "none"
        assert len(result.would_execute) == 4
        assert any("dry-run" in cmd for cmd in result.would_execute)
        assert any("depends" in cmd for cmd in result.would_execute)
        assert any("audit" in cmd for cmd in result.would_execute)
        assert any("showhold" in cmd for cmd in result.would_execute)


# =============================================================================
# PACKAGE_CAPABILITIES export
# =============================================================================


class TestPackageCapabilitiesExport:
    """Tests for the PACKAGE_CAPABILITIES list."""

    def test_all_capabilities_exported(self):
        """All five capability classes are in the export list."""
        assert len(PACKAGE_CAPABILITIES) == 5
        assert PackageInstallCapability in PACKAGE_CAPABILITIES
        assert PackageRemoveCapability in PACKAGE_CAPABILITIES
        assert PackageUpdateCapability in PACKAGE_CAPABILITIES
        assert PackageInfoCapability in PACKAGE_CAPABILITIES
        assert PackageConflictDetectCapability in PACKAGE_CAPABILITIES

    def test_all_capabilities_instantiate(self):
        """Every exported capability class can be instantiated with correct domain."""
        names = set()
        for cls in PACKAGE_CAPABILITIES:
            cap = cls()
            assert cap.spec.domain == "package"
            assert cap.spec.trust_level == "core"
            names.add(cap.spec.name)

        expected = {
            "package.install",
            "package.remove",
            "package.update",
            "package.info",
            "package.detect-conflicts",
        }
        assert names == expected


# =============================================================================
# Output Model Defaults
# =============================================================================


class TestOutputModelDefaults:
    """Tests for default values on output Pydantic models."""

    def test_install_output_defaults(self):
        """PackageInstallOutput defaults."""
        out = PackageInstallOutput(package="nginx")
        assert out.version == ""
        assert out.already_installed is False

    def test_remove_output_defaults(self):
        """PackageRemoveOutput defaults."""
        out = PackageRemoveOutput(package="nginx")
        assert out.was_installed is True

    def test_update_output_defaults(self):
        """PackageUpdateOutput defaults."""
        out = PackageUpdateOutput()
        assert out.packages_updated == 0

    def test_upgrade_output_defaults(self):
        """PackageUpgradeOutput defaults."""
        out = PackageUpgradeOutput()
        assert out.packages_upgraded == 0
        assert out.packages == ()

    def test_info_output_defaults(self):
        """PackageInfoOutput defaults."""
        out = PackageInfoOutput(package="nginx", installed=True)
        assert out.version == ""
        assert out.description == ""
        assert out.size == ""

    def test_conflict_output_defaults(self):
        """PackageConflictOutput defaults."""
        out = PackageConflictOutput(package="nginx", has_conflicts=False)
        assert out.conflicts == ()
        assert out.broken_deps == ()
        assert out.held_packages == ()
        assert out.recommendations == ()

    def test_conflict_detail_defaults(self):
        """ConflictDetail defaults."""
        detail = ConflictDetail(
            conflicting_package="libfoo",
            conflict_type="depends",
        )
        assert detail.installed_version is None
        assert detail.required_version is None
        assert detail.resolution == ""
