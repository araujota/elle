"""Tests for preflight apt validator."""

from unittest.mock import MagicMock, patch

import pytest

from elle.ops.preflight.apt_validator import (
    AptValidator,
    _extract_packages,
    _parse_size_to_mb,
    create_apt_validator,
)
from elle.ops.preflight.models import (
    IssueSeverity,
    PreflightStatus,
    PreflightTest,
)


class TestParseSizeToMb:
    """Tests for _parse_size_to_mb helper."""

    def test_bytes(self):
        """Test parsing bytes."""
        assert _parse_size_to_mb("1048576", "B") == pytest.approx(1.0, rel=0.01)

    def test_kilobytes(self):
        """Test parsing kilobytes."""
        assert _parse_size_to_mb("1024", "kB") == pytest.approx(1.0, rel=0.01)

    def test_megabytes(self):
        """Test parsing megabytes."""
        assert _parse_size_to_mb("5.5", "MB") == pytest.approx(5.5, rel=0.01)

    def test_gigabytes(self):
        """Test parsing gigabytes."""
        assert _parse_size_to_mb("1", "GB") == pytest.approx(1024.0, rel=0.01)

    def test_comma_separator(self):
        """Test parsing with comma separator."""
        assert _parse_size_to_mb("1,024", "MB") == pytest.approx(1024.0, rel=0.01)

    def test_invalid_value(self):
        """Test parsing invalid value."""
        assert _parse_size_to_mb("invalid", "MB") == 0.0


class TestExtractPackages:
    """Tests for _extract_packages helper."""

    def test_no_match(self):
        """Test with no match."""
        assert _extract_packages(None) == ()

    def test_single_package(self):
        """Test extracting single package."""
        import re

        pattern = re.compile(r"install:\s*\n((?:\s+\S+)+)")
        match = pattern.search("install:\n  nginx")
        result = _extract_packages(match)
        assert "nginx" in result

    def test_multiple_packages(self):
        """Test extracting multiple packages."""
        import re

        pattern = re.compile(r"install:\s*\n((?:\s+\S+)+)")
        match = pattern.search("install:\n  nginx curl vim")
        result = _extract_packages(match)
        assert len(result) == 3


class TestAptValidator:
    """Tests for AptValidator class."""

    def test_creation(self):
        """Test basic validator creation."""
        validator = AptValidator()
        assert validator._timeout == 60

    def test_custom_timeout(self):
        """Test validator with custom timeout."""
        validator = AptValidator(timeout_sec=120)
        assert validator._timeout == 120

    def test_build_apt_command_install(self):
        """Test building apt command for install."""
        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        cmd = validator._build_apt_command(test)
        assert cmd[0] == "apt-get"
        assert "--simulate" in cmd
        assert "install" in cmd
        assert "nginx" in cmd

    def test_build_apt_command_remove(self):
        """Test building apt command for remove."""
        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="remove",
        )
        cmd = validator._build_apt_command(test)
        assert "remove" in cmd
        assert "--purge" in cmd

    def test_build_apt_command_upgrade(self):
        """Test building apt command for upgrade."""
        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="upgrade",
        )
        cmd = validator._build_apt_command(test)
        assert "upgrade" in cmd

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_success(self, mock_run):
        """Test successful validation."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="The following NEW packages will be installed:\n  nginx nginx-common",
            stderr="",
        )

        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.SAFE
        assert result.tier_used == 1
        assert result.can_proceed is True
        assert "nginx" in result.packages_to_install

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_package_not_found(self, mock_run):
        """Test validation with package not found."""
        mock_run.return_value = MagicMock(
            returncode=100,
            stdout="",
            stderr="E: Unable to locate package nonexistent-package",
        )

        validator = AptValidator()
        test = PreflightTest(
            packages=("nonexistent-package",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False
        assert any(i.severity == IssueSeverity.ERROR and "not found" in i.message.lower() for i in result.issues)

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_dependency_conflict(self, mock_run):
        """Test validation with dependency conflict."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="The following packages have unmet dependencies:\n  nginx: Depends: libssl",
            stderr="",
        )

        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False
        assert any(i.severity == IssueSeverity.ERROR and "dependencies" in i.message.lower() for i in result.issues)

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_held_packages(self, mock_run):
        """Test validation with held packages."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="The following packages have been kept back:\n  nginx",
            stderr="",
        )

        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="upgrade",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.WARNING
        assert result.can_proceed is True
        assert any(i.severity == IssueSeverity.WARNING and "held" in i.message.lower() for i in result.issues)

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_disk_space(self, mock_run):
        """Test validation parses disk space."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Need to get 5.5 MB of archives.\nAfter this operation, 15.2 MB of additional disk space",
            stderr="",
        )

        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.download_size_mb == pytest.approx(5.5, rel=0.01)
        assert result.disk_space_required_mb == pytest.approx(15.2, rel=0.01)

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_disk_freed(self, mock_run):
        """Test validation parses disk space freed."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="After this operation, 50 MB disk space will be freed",
            stderr="",
        )

        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="remove",
        )
        result = validator.validate(test)

        assert result.disk_space_required_mb == pytest.approx(-50.0, rel=0.01)

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_large_operation_warning(self, mock_run):
        """Test validation warns on large operations."""
        # Build output with 60 packages
        packages = " ".join([f"pkg{i}" for i in range(60)])
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"The following NEW packages will be installed:\n  {packages}",
            stderr="",
        )

        validator = AptValidator()
        test = PreflightTest(
            packages=("some-meta-package",),
            operation="install",
        )
        result = validator.validate(test)

        assert any(i.severity == IssueSeverity.WARNING and "Large operation" in i.message for i in result.issues)

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_timeout(self, mock_run):
        """Test validation handles timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="apt", timeout=60)

        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False
        assert any(
            i.severity == IssueSeverity.ERROR and ("timed out" in i.message.lower() or "timeout" in i.message.lower())
            for i in result.issues
        )

    @patch("elle.ops.preflight.apt_validator.subprocess.run")
    def test_validate_exception(self, mock_run):
        """Test validation handles exceptions."""
        mock_run.side_effect = Exception("Some error")

        validator = AptValidator()
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False


class TestCreateAptValidator:
    """Tests for create_apt_validator factory function."""

    def test_create_default(self):
        """Test creating validator with defaults."""
        validator = create_apt_validator()
        assert isinstance(validator, AptValidator)

    def test_create_custom_timeout(self):
        """Test creating validator with custom timeout."""
        validator = create_apt_validator(timeout_sec=120)
        assert validator._timeout == 120
