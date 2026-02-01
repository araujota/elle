"""Tests for preflight validator orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.ops.preflight.models import (
    IssueSeverity,
    PreflightConfig,
    PreflightIssue,
    PreflightResult,
    PreflightStatus,
    PreflightTest,
)
from elle.ops.preflight.risk_classifier import RiskLevel
from elle.ops.preflight.validator import (
    PreflightValidator,
    format_result_for_display,
    get_validator,
    validate_packages,
)


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

            validate_packages(["nginx", "curl"])

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


class TestSelectTierBranches:
    """Tests for _select_tier covering all risk-level branches."""

    def test_select_tier_none_risk(self):
        """Test tier selection for NONE risk returns tier 1."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("vim",), operation="install")
        tier = validator._select_tier(RiskLevel.NONE, test)
        assert tier == 1

    def test_select_tier_medium_risk(self):
        """Test tier selection for MEDIUM risk returns tier 1."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("vim",), operation="install")
        tier = validator._select_tier(RiskLevel.MEDIUM, test)
        assert tier == 1

    @patch("elle.ops.preflight.validator.can_use_container_validation", return_value=False)
    def test_select_tier_high_risk_no_container(self, mock_can_use):
        """Test tier selection for HIGH risk when container unavailable returns tier 1."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("linux-image-generic",), operation="install")
        tier = validator._select_tier(RiskLevel.HIGH, test)
        assert tier == 1
        mock_can_use.assert_called_once()

    @patch("elle.ops.preflight.validator.can_use_container_validation", return_value=True)
    def test_select_tier_high_risk_with_container(self, mock_can_use):
        """Test tier selection for HIGH risk when container available returns tier 2."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("nginx",), operation="install")
        tier = validator._select_tier(RiskLevel.HIGH, test)
        assert tier == 2

    @patch("elle.ops.preflight.validator.can_use_container_validation", return_value=False)
    def test_select_tier_critical_no_container(self, mock_can_use):
        """Test tier selection for CRITICAL risk when container unavailable returns tier 1."""
        validator = PreflightValidator()
        test = PreflightTest(packages=("linux-image-generic",), operation="install")
        tier = validator._select_tier(RiskLevel.CRITICAL, test)
        assert tier == 1


class TestRunTieredValidation:
    """Tests for _run_tiered_validation covering all tier paths."""

    def _make_validator(self):
        """Create a validator with mocked sub-validators."""
        config = PreflightConfig(skip_low_risk=False)
        validator = PreflightValidator(config=config)
        validator._apt_validator = MagicMock()
        validator._nspawn_validator = MagicMock()
        return validator

    def test_apt_blocked_stops_early(self):
        """When apt validation returns BLOCKED, higher tiers are not run."""
        validator = self._make_validator()
        blocked_result = PreflightResult(
            status=PreflightStatus.BLOCKED,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.ERROR,
                    message="Broken dependencies",
                    source="apt",
                ),
            ),
            duration_ms=50,
            can_proceed=False,
        )
        validator._apt_validator.validate.return_value = blocked_result

        test = PreflightTest(packages=("nginx",), operation="install")
        result = validator._run_tiered_validation(test, tier=2, risk=RiskLevel.HIGH)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False
        validator._nspawn_validator.validate.assert_not_called()

    def test_tier1_high_risk_adds_warning(self):
        """Tier 1 with HIGH risk adds a container-recommended warning."""
        validator = self._make_validator()
        apt_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            apt_output="dry run output",
            packages_to_install=("nginx",),
            download_size_mb=5.0,
            disk_space_required_mb=10.0,
        )
        validator._apt_validator.validate.return_value = apt_result

        test = PreflightTest(packages=("nginx",), operation="install")
        result = validator._run_tiered_validation(test, tier=1, risk=RiskLevel.HIGH)

        assert result.status == PreflightStatus.WARNING
        assert result.tier_used == 1
        assert result.can_proceed is True
        assert any("container validation recommended" in i.message for i in result.issues)
        assert result.apt_output == "dry run output"
        assert result.packages_to_install == ("nginx",)

    def test_tier1_critical_risk_adds_warning(self):
        """Tier 1 with CRITICAL risk adds a container-recommended warning."""
        validator = self._make_validator()
        apt_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        validator._apt_validator.validate.return_value = apt_result

        test = PreflightTest(packages=("systemd",), operation="install")
        result = validator._run_tiered_validation(test, tier=1, risk=RiskLevel.CRITICAL)

        assert result.status == PreflightStatus.WARNING
        assert any("container validation recommended" in i.message for i in result.issues)

    def test_tier1_low_risk_no_extra_warning(self):
        """Tier 1 with LOW risk does not add extra warnings."""
        validator = self._make_validator()
        apt_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        validator._apt_validator.validate.return_value = apt_result

        test = PreflightTest(packages=("vim",), operation="install")
        result = validator._run_tiered_validation(test, tier=1, risk=RiskLevel.LOW)

        assert result.status == PreflightStatus.SAFE
        assert result.can_proceed is True

    def test_tier2_combines_results_safe(self):
        """Tier 2 combines apt and nspawn results when both pass cleanly."""
        validator = self._make_validator()
        apt_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            apt_output="apt output",
            packages_to_install=("nginx",),
            packages_to_upgrade=("openssl",),
            packages_to_remove=(),
            download_size_mb=5.0,
            disk_space_required_mb=10.0,
        )
        nspawn_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=2,
            duration_ms=3000,
            can_proceed=True,
            nspawn_output="nspawn output",
        )
        validator._apt_validator.validate.return_value = apt_result
        validator._nspawn_validator.validate.return_value = nspawn_result

        test = PreflightTest(packages=("nginx",), operation="install")
        result = validator._run_tiered_validation(test, tier=2, risk=RiskLevel.HIGH)

        assert result.status == PreflightStatus.SAFE
        assert result.tier_used == 2
        assert result.can_proceed is True
        assert result.duration_ms == 3100
        assert result.apt_output == "apt output"
        assert result.nspawn_output == "nspawn output"
        assert result.packages_to_install == ("nginx",)

    def test_tier2_combines_results_with_warnings(self):
        """Tier 2 returns WARNING when non-error issues exist."""
        validator = self._make_validator()
        apt_result = PreflightResult(
            status=PreflightStatus.WARNING,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.WARNING,
                    message="Package held back",
                    source="apt",
                ),
            ),
            duration_ms=100,
            can_proceed=True,
        )
        nspawn_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=2,
            duration_ms=2000,
            can_proceed=True,
        )
        validator._apt_validator.validate.return_value = apt_result
        validator._nspawn_validator.validate.return_value = nspawn_result

        test = PreflightTest(packages=("nginx",), operation="install")
        result = validator._run_tiered_validation(test, tier=2, risk=RiskLevel.HIGH)

        assert result.status == PreflightStatus.WARNING
        assert result.can_proceed is True
        assert len(result.issues) == 1

    def test_tier2_combines_results_with_errors(self):
        """Tier 2 returns BLOCKED when errors exist in combined issues."""
        validator = self._make_validator()
        apt_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        nspawn_result = PreflightResult(
            status=PreflightStatus.BLOCKED,
            tier_used=2,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.ERROR,
                    message="Service failed to start in container",
                    source="nspawn",
                ),
            ),
            duration_ms=5000,
            can_proceed=False,
        )
        validator._apt_validator.validate.return_value = apt_result
        validator._nspawn_validator.validate.return_value = nspawn_result

        test = PreflightTest(packages=("nginx",), operation="install")
        result = validator._run_tiered_validation(test, tier=2, risk=RiskLevel.HIGH)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False
        assert result.tier_used == 2

    def test_tier2_with_critical_issue(self):
        """Tier 2 returns BLOCKED when CRITICAL severity issue exists."""
        validator = self._make_validator()
        apt_result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        nspawn_result = PreflightResult(
            status=PreflightStatus.BLOCKED,
            tier_used=2,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.CRITICAL,
                    message="System broken after install",
                    source="nspawn",
                ),
            ),
            duration_ms=5000,
            can_proceed=False,
        )
        validator._apt_validator.validate.return_value = apt_result
        validator._nspawn_validator.validate.return_value = nspawn_result

        test = PreflightTest(packages=("nginx",), operation="install")
        result = validator._run_tiered_validation(test, tier=2, risk=RiskLevel.HIGH)

        assert result.status == PreflightStatus.BLOCKED
        assert result.can_proceed is False


class TestValidateFullFlow:
    """Tests for the full validate() method with various branch paths."""

    def test_validate_auto_tier_disabled_uses_default(self):
        """When auto_tier_selection is False, default_tier is used."""
        config = PreflightConfig(
            skip_low_risk=False,
            auto_tier_selection=False,
            default_tier=1,
        )
        validator = PreflightValidator(config=config)
        validator._apt_validator = MagicMock()
        validator._apt_validator.validate.return_value = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )

        test = PreflightTest(packages=("nginx",), operation="install")
        result = validator.validate(test)

        assert result.tier_used == 1
        assert result.can_proceed is True

    def test_validate_force_tier_overrides_auto(self):
        """force_tier in test overrides auto tier selection."""
        config = PreflightConfig(skip_low_risk=False)
        validator = PreflightValidator(config=config)
        validator._apt_validator = MagicMock()
        validator._nspawn_validator = MagicMock()
        validator._apt_validator.validate.return_value = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        validator._nspawn_validator.validate.return_value = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=2,
            duration_ms=3000,
            can_proceed=True,
        )

        test = PreflightTest(packages=("vim",), operation="install", force_tier=2)
        result = validator.validate(test)

        assert result.tier_used == 2
        validator._nspawn_validator.validate.assert_called_once()

    @patch(
        "elle.ops.preflight.validator.classify_risk",
        return_value=MagicMock(level=RiskLevel.NONE),
    )
    def test_validate_skip_low_risk_with_none_risk(self, mock_classify):
        """skip_low_risk skips when risk is NONE and tier is 1."""
        config = PreflightConfig(skip_low_risk=True)
        validator = PreflightValidator(config=config)
        validator._apt_validator = MagicMock()

        test = PreflightTest(packages=("somepackage",), operation="install")
        result = validator.validate(test)

        assert result.status == PreflightStatus.SKIPPED
        assert result.can_proceed is True
        validator._apt_validator.validate.assert_not_called()


class TestFormatResultBranches:
    """Additional tests for format_result_for_display covering uncovered branches."""

    def test_format_skipped_tier_name(self):
        """Tier 0 displays as 'Skipped'."""
        result = PreflightResult(
            status=PreflightStatus.SKIPPED,
            tier_used=0,
            duration_ms=0,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "Skipped" in output

    def test_format_tier3_name(self):
        """Tier 3 displays as 'LXD'."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=3,
            duration_ms=100,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "LXD" in output

    def test_format_packages_to_remove(self):
        """Packages to remove are displayed."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            packages_to_remove=("nginx", "nginx-common", "nginx-full"),
        )
        output = format_result_for_display(result, use_colors=False)
        assert "remove" in output.lower()
        assert "nginx" in output
        assert "- nginx" in output

    def test_format_many_install_packages_overflow(self):
        """More than 5 packages to install shows overflow message."""
        pkgs = tuple(f"pkg{i}" for i in range(8))
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            packages_to_install=pkgs,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "... and 3 more" in output

    def test_format_many_upgrade_packages_overflow(self):
        """More than 5 packages to upgrade shows overflow message."""
        pkgs = tuple(f"pkg{i}" for i in range(7))
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            packages_to_upgrade=pkgs,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "... and 2 more" in output

    def test_format_many_remove_packages_overflow(self):
        """More than 5 packages to remove shows overflow message."""
        pkgs = tuple(f"pkg{i}" for i in range(10))
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            packages_to_remove=pkgs,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "... and 5 more" in output

    def test_format_negative_disk_space(self):
        """Negative disk_space_required_mb shows 'freed' message."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
            disk_space_required_mb=-12.5,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "freed" in output.lower()
        assert "12.5" in output

    def test_format_with_colors_safe(self):
        """Test formatting with colors enabled for SAFE status."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=True)
        # Should contain ANSI escape sequences
        assert "\033[" in output
        assert "SAFE" in output

    def test_format_with_colors_blocked(self):
        """Test formatting with colors enabled for BLOCKED status and can_proceed=False."""
        result = PreflightResult(
            status=PreflightStatus.BLOCKED,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.ERROR,
                    message="Broken",
                    source="apt",
                ),
            ),
            duration_ms=100,
            can_proceed=False,
        )
        output = format_result_for_display(result, use_colors=True)
        assert "\033[" in output
        assert "Should not proceed" in output

    def test_format_with_colors_issues(self):
        """Test formatting issues with colors enabled covering severity color branches."""
        result = PreflightResult(
            status=PreflightStatus.WARNING,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.INFO,
                    message="Informational note",
                    source="apt",
                ),
                PreflightIssue(
                    severity=IssueSeverity.WARNING,
                    message="Watch out",
                    source="apt",
                ),
                PreflightIssue(
                    severity=IssueSeverity.ERROR,
                    message="Something broke",
                    source="apt",
                ),
                PreflightIssue(
                    severity=IssueSeverity.CRITICAL,
                    message="System at risk",
                    source="apt",
                ),
            ),
            duration_ms=100,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=True)
        assert "Informational note" in output
        assert "Watch out" in output
        assert "Something broke" in output
        assert "System at risk" in output

    def test_format_with_colors_import_failure(self):
        """When Colors import fails, use_colors falls back to False."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        with patch.dict("sys.modules", {"elle.cli.terminal.renderer": None}):
            output = format_result_for_display(result, use_colors=True)
        # Should still produce valid output without ANSI escapes
        assert "SAFE" in output
        assert "Safe to proceed" in output

    def test_format_issue_with_recommendation(self):
        """Issues with recommendations display the arrow prefix."""
        result = PreflightResult(
            status=PreflightStatus.WARNING,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.WARNING,
                    message="Version pinned",
                    recommendation="Run apt-mark unhold first",
                    source="apt",
                ),
            ),
            duration_ms=50,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "Version pinned" in output
        assert "Run apt-mark unhold first" in output

    def test_format_issue_without_recommendation(self):
        """Issues without recommendations do not display arrow prefix."""
        result = PreflightResult(
            status=PreflightStatus.WARNING,
            tier_used=1,
            issues=(
                PreflightIssue(
                    severity=IssueSeverity.INFO,
                    message="Just info",
                    source="apt",
                ),
            ),
            duration_ms=50,
            can_proceed=True,
        )
        output = format_result_for_display(result, use_colors=False)
        assert "Just info" in output
        # The arrow recommendation line should not appear
        lines = output.split("\n")
        assert not any(line.strip().startswith("\u2192 ") and "Just info" not in line for line in lines)


class TestGetValidatorBranches:
    """Additional tests for get_validator covering config passing."""

    def test_get_validator_with_config(self):
        """Test get_validator passes config on first creation."""
        import elle.ops.preflight.validator as module

        module._validator = None

        config = PreflightConfig(enabled=False)
        validator = get_validator(config=config)
        assert validator._config.enabled is False

        # Reset for other tests
        module._validator = None

    def test_get_validator_ignores_config_on_subsequent(self):
        """Test get_validator ignores config after first creation."""
        import elle.ops.preflight.validator as module

        module._validator = None

        config1 = PreflightConfig(enabled=True)
        v1 = get_validator(config=config1)

        config2 = PreflightConfig(enabled=False)
        v2 = get_validator(config=config2)

        assert v1 is v2
        assert v2._config.enabled is True

        # Reset for other tests
        module._validator = None


class TestValidatePackagesBranches:
    """Additional tests for validate_packages covering branch paths."""

    def test_validate_packages_with_tuple(self):
        """Test validate_packages when passed a tuple (no list conversion needed)."""
        with patch("elle.ops.preflight.validator.get_validator") as mock_get:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = PreflightResult(
                status=PreflightStatus.SAFE,
                tier_used=1,
                duration_ms=100,
                can_proceed=True,
            )
            mock_get.return_value = mock_validator

            result = validate_packages(("nginx", "curl"))

            assert result.status == PreflightStatus.SAFE
            call_args = mock_validator.validate.call_args[0][0]
            assert call_args.packages == ("nginx", "curl")

    def test_validate_packages_upgrade_operation(self):
        """Test validate_packages with upgrade operation."""
        with patch("elle.ops.preflight.validator.get_validator") as mock_get:
            mock_validator = MagicMock()
            mock_validator.validate.return_value = PreflightResult(
                status=PreflightStatus.SAFE,
                tier_used=1,
                duration_ms=100,
                can_proceed=True,
            )
            mock_get.return_value = mock_validator

            validate_packages(["openssl"], operation="upgrade", force_tier=1)

            call_args = mock_validator.validate.call_args[0][0]
            assert call_args.operation == "upgrade"
            assert call_args.force_tier == 1
