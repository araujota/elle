"""Tests for preflight validator orchestrator."""

import pytest
from unittest.mock import MagicMock, patch

from elle.ops.preflight.validator import (
    PreflightValidator,
    get_validator,
    validate_packages,
    format_result_for_display,
)
from elle.ops.preflight.models import (
    IssueSeverity,
    PreflightConfig,
    PreflightIssue,
    PreflightResult,
    PreflightStatus,
    PreflightTest,
)
from elle.ops.preflight.risk_classifier import RiskLevel


class TestPreflightValidator:
    """Tests for PreflightValidator class."""

    def test_creation_default_config(self):
        """Test validator creation with default config."""
        validator = PreflightValidator()
        assert validator._config.enabled is True

    def test_creation_custom_config(self):
        """Test validator creation with custom config."""
        config = PreflightConfig(enabled=False)
        validator = PreflightValidator(config=config)
        assert validator._config.enabled is False

    def test_lazy_apt_validator(self):
        """Test lazy initialization of apt validator."""
        validator = PreflightValidator()
        assert validator._apt_validator is None
        apt_val = validator.apt_validator
        assert apt_val is not None
        assert validator._apt_validator is apt_val

    def test_lazy_nspawn_validator(self):
        """Test lazy initialization of nspawn validator."""
        validator = PreflightValidator()
        assert validator._nspawn_validator is None
        nspawn_val = validator.nspawn_validator
        assert nspawn_val is not None
        assert validator._nspawn_validator is nspawn_val

    def test_validate_disabled(self):
        """Test validation when disabled."""
        config = PreflightConfig(enabled=False)
        validator = PreflightValidator(config=config)

        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.SKIPPED
        assert result.tier_used == 0
        assert result.can_proceed is True

    def test_validate_tier1_low_risk(self):
        """Test tier 1 validation for low-risk package."""
        config = PreflightConfig(skip_low_risk=False)  # Don't skip so we test tier 1
        validator = PreflightValidator(config=config)
        validator._apt_validator = MagicMock()
        validator._apt_validator.validate.return_value = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )

        test = PreflightTest(
            packages=("vim",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.tier_used == 1
        assert result.can_proceed is True

    def test_validate_skip_low_risk(self):
        """Test skipping validation for low-risk packages."""
        config = PreflightConfig(skip_low_risk=True)
        validator = PreflightValidator(config=config)
        validator._apt_validator = MagicMock()

        test = PreflightTest(
            packages=("vim",),
            operation="install",
        )
        result = validator.validate(test)

        assert result.status == PreflightStatus.SKIPPED
        assert result.can_proceed is True
        # For skipped low-risk, apt validator should not be called
        validator._apt_validator.validate.assert_not_called()

    def test_select_tier_low_risk(self):
        """Test tier selection for low-risk packages."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("vim",), operation="install")
        tier = validator._select_tier(RiskLevel.LOW, test)
        assert tier == 1

    def test_select_tier_high_risk(self):
        """Test tier selection for high-risk packages."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("nginx",), operation="install")
        tier = validator._select_tier(RiskLevel.HIGH, test)
        assert tier == 2

    def test_select_tier_critical_risk(self):
        """Test tier selection for critical-risk packages."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("nginx",), operation="install")  # Can use container
        tier = validator._select_tier(RiskLevel.CRITICAL, test)
        assert tier == 2  # Falls back to 2 since tier 3 not implemented

    def test_force_tier(self):
        """Test forcing a specific tier."""
        config = PreflightConfig(skip_low_risk=False)  # Don't skip so we can test tier
        validator = PreflightValidator(config=config)
        validator._apt_validator = MagicMock()
        validator._apt_validator.validate.return_value = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )

        test = PreflightTest(
            packages=("vim",),
            operation="install",
            force_tier=1,
        )
        result = validator.validate(test)

        # Should use forced tier regardless of risk
        assert result.tier_used == 1


class TestValidatePackages:
    """Tests for validate_packages convenience function."""

    def test_basic_validation(self):
        """Test basic package validation."""
        with patch("elle.ops.preflight.validator.get_validator") as mock_get:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = PreflightResult(
                status=PreflightStatus.SAFE,
                tier_used=1,
                duration_ms=100,
                can_proceed=True,
            )
            mock_get.return_value = mock_validator

            result = validate_packages(("nginx",))

            assert result.status == PreflightStatus.SAFE
            mock_validator.validate.assert_called_once()

    def test_list_input_conversion(self):
        """Test that list input is converted to tuple."""
        with patch("elle.ops.preflight.validator.get_validator") as mock_get:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = PreflightResult(
                status=PreflightStatus.SAFE,
                tier_used=1,
                duration_ms=100,
                can_proceed=True,
            )
            mock_get.return_value = mock_validator

            result = validate_packages(["nginx", "curl"])

            # Verify it was called with a tuple
            call_args = mock_validator.validate.call_args[0][0]
            assert isinstance(call_args.packages, tuple)

    def test_with_operation(self):
        """Test validation with specific operation."""
        with patch("elle.ops.preflight.validator.get_validator") as mock_get:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = PreflightResult(
                status=PreflightStatus.SAFE,
                tier_used=1,
                duration_ms=100,
                can_proceed=True,
            )
            mock_get.return_value = mock_validator

            validate_packages(("nginx",), operation="remove")

            call_args = mock_validator.validate.call_args[0][0]
            assert call_args.operation == "remove"

    def test_with_force_tier(self):
        """Test validation with forced tier."""
        with patch("elle.ops.preflight.validator.get_validator") as mock_get:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = PreflightResult(
                status=PreflightStatus.SAFE,
                tier_used=2,
                duration_ms=100,
                can_proceed=True,
            )
            mock_get.return_value = mock_validator

            validate_packages(("nginx",), force_tier=2)

            call_args = mock_validator.validate.call_args[0][0]
            assert call_args.force_tier == 2


class TestFormatResultForDisplay:
    """Tests for format_result_for_display function."""

    def test_format_safe_result(self):
        """Test formatting a safe result."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=False)

        assert "SAFE" in output
        assert "Safe to proceed" in output
        assert "apt --dry-run" in output

    def test_format_blocked_result(self):
        """Test formatting a blocked result."""
        result = PreflightResult(
            status=PreflightStatus.BLOCKED,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.ERROR,
                    message="Package not found",
                ),
            ),
            duration_ms=100,
            can_proceed=False,
        )
        output = format_result_for_display(result, use_colors=False)

        assert "BLOCKED" in output
        assert "Should not proceed" in output
        assert "Package not found" in output

    def test_format_with_packages(self):
        """Test formatting result with package lists."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            packages_to_install=("nginx", "nginx-common"),
            packages_to_upgrade=("openssl",),
        )
        output = format_result_for_display(result, use_colors=False)

        assert "install" in output.lower()
        assert "nginx" in output
        assert "upgrade" in output.lower()
        assert "openssl" in output

    def test_format_with_disk_space(self):
        """Test formatting result with disk space info."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            download_size_mb=5.5,
            disk_space_required_mb=15.2,
        )
        output = format_result_for_display(result, use_colors=False)

        assert "5.5" in output
        assert "15.2" in output

    def test_format_with_issues(self):
        """Test formatting result with issues."""
        result = PreflightResult(
            status=PreflightStatus.WARNING,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.WARNING,
                    message="Package held back",
                    recommendation="Use apt-mark unhold",
                ),
            ),
            duration_ms=100,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=False)

        assert "Package held back" in output
        assert "apt-mark unhold" in output

    def test_format_tier2_result(self):
        """Test formatting tier 2 result."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=2,
            duration_ms=5000,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=False)

        assert "systemd-nspawn" in output


class TestGetValidator:
    """Tests for get_validator singleton function."""

    def test_returns_validator(self):
        """Test that get_validator returns a validator."""
        # Reset the singleton first
        import elle.ops.preflight.validator as module
        module._validator = None

        validator = get_validator()
        assert isinstance(validator, PreflightValidator)

    def test_singleton(self):
        """Test that get_validator returns same instance."""
        import elle.ops.preflight.validator as module
        module._validator = None

        v1 = get_validator()
        v2 = get_validator()
        assert v1 is v2
