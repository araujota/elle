"""Tests for preflight models."""

import pytest
from datetime import datetime

from elle.ops.preflight.models import (
    IssueSeverity,
    NspawnConfig,
    PreflightConfig,
    PreflightIssue,
    PreflightResult,
    PreflightStatus,
    PreflightTest,
)


class TestPreflightStatus:
    """Tests for PreflightStatus enum."""

    def test_status_values(self):
        """Test that all status values exist."""
        assert PreflightStatus.SAFE == "safe"
        assert PreflightStatus.WARNING == "warning"
        assert PreflightStatus.BLOCKED == "blocked"
        assert PreflightStatus.SKIPPED == "skipped"


class TestIssueSeverity:
    """Tests for IssueSeverity enum."""

    def test_severity_values(self):
        """Test that all severity values exist."""
        assert IssueSeverity.INFO == "info"
        assert IssueSeverity.WARNING == "warning"
        assert IssueSeverity.ERROR == "error"
        assert IssueSeverity.CRITICAL == "critical"


class TestPreflightIssue:
    """Tests for PreflightIssue model."""

    def test_basic_creation(self):
        """Test basic issue creation."""
        issue = PreflightIssue(
            severity=IssueSeverity.WARNING,
            message="Package held back",
        )
        assert issue.severity == IssueSeverity.WARNING
        assert issue.message == "Package held back"
        assert issue.recommendation is None
        assert issue.source == "apt"

    def test_with_all_fields(self):
        """Test issue with all fields."""
        issue = PreflightIssue(
            severity=IssueSeverity.ERROR,
            message="Dependency conflict",
            recommendation="Remove conflicting package",
            source="apt",
            package="nginx",
            technical_detail="nginx conflicts with apache2",
        )
        assert issue.package == "nginx"
        assert issue.technical_detail == "nginx conflicts with apache2"

    def test_immutable(self):
        """Test that PreflightIssue is immutable."""
        issue = PreflightIssue(
            severity=IssueSeverity.INFO,
            message="Test",
        )
        with pytest.raises(Exception):
            issue.message = "changed"


class TestPreflightResult:
    """Tests for PreflightResult model."""

    def test_basic_creation(self):
        """Test basic result creation."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        assert result.status == PreflightStatus.SAFE
        assert result.tier_used == 1
        assert result.can_proceed is True
        assert result.issues == ()

    def test_with_issues(self):
        """Test result with issues."""
        issues = (
            PreflightIssue(
                severity=IssueSeverity.WARNING,
                message="Large operation",
            ),
        )
        result = PreflightResult(
            status=PreflightStatus.WARNING,
            tier_used=1,
            issues=issues,
            duration_ms=150,
            can_proceed=True,
        )
        assert len(result.issues) == 1
        assert result.has_warnings is True
        assert result.has_errors is False

    def test_has_errors(self):
        """Test has_errors property."""
        issues = (
            PreflightIssue(
                severity=IssueSeverity.ERROR,
                message="Install failed",
            ),
        )
        result = PreflightResult(
            status=PreflightStatus.BLOCKED,
            tier_used=2,
            issues=issues,
            duration_ms=5000,
            can_proceed=False,
        )
        assert result.has_errors is True
        assert result.error_count == 1

    def test_with_package_info(self):
        """Test result with package information."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=200,
            can_proceed=True,
            packages_to_install=("nginx", "nginx-common"),
            packages_to_upgrade=("openssl",),
            packages_to_remove=(),
            download_size_mb=5.5,
            disk_space_required_mb=15.2,
        )
        assert result.packages_to_install == ("nginx", "nginx-common")
        assert result.download_size_mb == 5.5

    def test_tier_constraint(self):
        """Test tier value constraints."""
        # Valid tiers
        for tier in (1, 2, 3):
            result = PreflightResult(
                status=PreflightStatus.SAFE,
                tier_used=tier,
                duration_ms=100,
                can_proceed=True,
            )
            assert result.tier_used == tier

    def test_validated_at_timestamp(self):
        """Test that validated_at is set automatically."""
        result = PreflightResult(
            status=PreflightStatus.SAFE,
            tier_used=1,
            duration_ms=100,
            can_proceed=True,
        )
        assert result.validated_at is not None
        assert isinstance(result.validated_at, datetime)


class TestPreflightTest:
    """Tests for PreflightTest model."""

    def test_basic_creation(self):
        """Test basic test specification creation."""
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
        )
        assert test.packages == ("nginx",)
        assert test.operation == "install"
        assert test.high_risk is False
        assert test.force_tier is None

    def test_multiple_packages(self):
        """Test test with multiple packages."""
        test = PreflightTest(
            packages=("nginx", "curl", "vim"),
            operation="install",
        )
        assert len(test.packages) == 3

    def test_operations(self):
        """Test valid operations."""
        for op in ("install", "upgrade", "remove"):
            test = PreflightTest(
                packages=("nginx",),
                operation=op,
            )
            assert test.operation == op

    def test_force_tier(self):
        """Test force_tier specification."""
        test = PreflightTest(
            packages=("nginx",),
            operation="install",
            force_tier=2,
        )
        assert test.force_tier == 2

    def test_high_risk_flag(self):
        """Test high_risk flag."""
        test = PreflightTest(
            packages=("systemd",),
            operation="upgrade",
            high_risk=True,
        )
        assert test.high_risk is True

    def test_with_versions(self):
        """Test test with version information."""
        test = PreflightTest(
            packages=("nginx",),
            operation="upgrade",
            current_versions={"nginx": "1.18.0"},
            target_versions={"nginx": "1.24.0"},
        )
        assert test.current_versions["nginx"] == "1.18.0"
        assert test.target_versions["nginx"] == "1.24.0"


class TestNspawnConfig:
    """Tests for NspawnConfig model."""

    def test_defaults(self):
        """Test default configuration."""
        config = NspawnConfig()
        assert config.base_image_path == "/var/lib/elle/preflight/base-image"
        assert config.timeout_sec == 120
        assert config.network_enabled is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = NspawnConfig(
            base_image_path="/custom/path",
            timeout_sec=300,
            network_enabled=False,
        )
        assert config.base_image_path == "/custom/path"
        assert config.timeout_sec == 300
        assert config.network_enabled is False


class TestPreflightConfig:
    """Tests for PreflightConfig model."""

    def test_defaults(self):
        """Test default configuration."""
        config = PreflightConfig()
        assert config.enabled is True
        assert config.auto_tier_selection is True
        assert config.default_tier == 1
        assert config.skip_low_risk is True

    def test_disabled(self):
        """Test disabled configuration."""
        config = PreflightConfig(enabled=False)
        assert config.enabled is False

    def test_custom_tier(self):
        """Test custom default tier."""
        config = PreflightConfig(
            auto_tier_selection=False,
            default_tier=2,
        )
        assert config.auto_tier_selection is False
        assert config.default_tier == 2

    def test_nested_nspawn_config(self):
        """Test nested nspawn configuration."""
        config = PreflightConfig(
            nspawn=NspawnConfig(timeout_sec=60),
        )
        assert config.nspawn.timeout_sec == 60
