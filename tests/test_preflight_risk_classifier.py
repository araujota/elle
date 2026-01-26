"""Tests for preflight risk classifier."""

import pytest

from elle.ops.preflight.risk_classifier import (
    CRITICAL_PACKAGES,
    HIGH_RISK_PACKAGES,
    NO_CONTAINER_PACKAGES,
    RiskAssessment,
    RiskLevel,
    can_use_container_validation,
    classify_risk,
    get_risk_summary,
    is_high_risk,
)


class TestRiskLevel:
    """Tests for RiskLevel enum."""

    def test_risk_level_values(self):
        """Test that all risk levels exist."""
        assert RiskLevel.NONE == "none"
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.CRITICAL == "critical"


class TestRiskAssessment:
    """Tests for RiskAssessment model."""

    def test_basic_creation(self):
        """Test basic assessment creation."""
        assessment = RiskAssessment(
            level=RiskLevel.LOW,
            recommended_tier=1,
        )
        assert assessment.level == RiskLevel.LOW
        assert assessment.recommended_tier == 1
        assert assessment.factors == ()
        assert assessment.require_confirmation is False
        assert assessment.allow_skip is True

    def test_with_factors(self):
        """Test assessment with factors."""
        assessment = RiskAssessment(
            level=RiskLevel.HIGH,
            factors=("systemd is critical", "may affect boot"),
            recommended_tier=2,
            require_confirmation=True,
            allow_skip=False,
        )
        assert len(assessment.factors) == 2
        assert assessment.require_confirmation is True
        assert assessment.allow_skip is False

    def test_immutable(self):
        """Test that RiskAssessment is immutable."""
        assessment = RiskAssessment(
            level=RiskLevel.LOW,
            recommended_tier=1,
        )
        with pytest.raises(Exception):
            assessment.level = RiskLevel.HIGH


class TestClassifyRisk:
    """Tests for classify_risk function."""

    def test_empty_packages(self):
        """Test classification with no packages."""
        assessment = classify_risk(())
        assert assessment.level == RiskLevel.NONE
        assert assessment.recommended_tier == 1

    def test_low_risk_package(self):
        """Test classification of low-risk package."""
        assessment = classify_risk(("vim",))
        assert assessment.level == RiskLevel.LOW
        assert assessment.recommended_tier == 1
        assert assessment.allow_skip is True

    def test_medium_risk_package(self):
        """Test classification of medium-risk package."""
        assessment = classify_risk(("python3",))
        assert assessment.level == RiskLevel.MEDIUM
        assert assessment.recommended_tier == 1

    def test_high_risk_package(self):
        """Test classification of high-risk package."""
        assessment = classify_risk(("nginx",))
        assert assessment.level == RiskLevel.HIGH
        assert assessment.recommended_tier == 2
        assert assessment.require_confirmation is True

    def test_critical_package(self):
        """Test classification of critical package."""
        assessment = classify_risk(("systemd",))
        assert assessment.level == RiskLevel.CRITICAL
        assert assessment.recommended_tier == 3
        assert assessment.require_confirmation is True
        assert assessment.allow_skip is False

    def test_multiple_packages_max_risk(self):
        """Test that classification uses max risk level."""
        # Mix of low and high risk
        assessment = classify_risk(("vim", "nginx"))
        assert assessment.level == RiskLevel.HIGH

        # Mix of low and critical
        assessment = classify_risk(("curl", "systemd"))
        assert assessment.level == RiskLevel.CRITICAL

    def test_remove_operation_increases_risk(self):
        """Test that remove operation increases risk."""
        # Low risk install
        install = classify_risk(("vim",), "install")
        # Remove should bump to medium
        remove = classify_risk(("vim",), "remove")
        assert remove.level.value > install.level.value

    def test_large_operation_increases_risk(self):
        """Test that large operations increase risk."""
        # Create list of 15 low-risk packages
        packages = tuple([f"pkg{i}" for i in range(15)])
        assessment = classify_risk(packages)
        # Should note large operation
        assert any("Large operation" in f for f in assessment.factors)

    def test_package_with_version_specifier(self):
        """Test classification with version specifier."""
        # Should normalize package name
        assessment = classify_risk(("nginx=1.24.0",))
        assert assessment.level == RiskLevel.HIGH

    def test_known_critical_packages(self):
        """Test that known critical packages are classified correctly."""
        critical_examples = ["systemd", "libc6", "apt", "grub-pc"]
        for pkg in critical_examples:
            assessment = classify_risk((pkg,))
            assert assessment.level == RiskLevel.CRITICAL, f"{pkg} should be critical"

    def test_known_high_risk_packages(self):
        """Test that known high-risk packages are classified correctly."""
        high_risk_examples = ["openssh-server", "postgresql", "docker.io"]
        for pkg in high_risk_examples:
            assessment = classify_risk((pkg,))
            assert assessment.level == RiskLevel.HIGH, f"{pkg} should be high risk"


class TestIsHighRisk:
    """Tests for is_high_risk convenience function."""

    def test_low_risk_package(self):
        """Test that low-risk package returns False."""
        assert is_high_risk(("vim",)) is False

    def test_high_risk_package(self):
        """Test that high-risk package returns True."""
        assert is_high_risk(("nginx",)) is True

    def test_critical_package(self):
        """Test that critical package returns True."""
        assert is_high_risk(("systemd",)) is True

    def test_list_input(self):
        """Test that list input works."""
        assert is_high_risk(["nginx"]) is True
        assert is_high_risk(["vim"]) is False


class TestCanUseContainerValidation:
    """Tests for can_use_container_validation function."""

    def test_regular_package(self):
        """Test that regular packages can use container validation."""
        assert can_use_container_validation(("nginx",)) is True
        assert can_use_container_validation(("vim", "curl")) is True

    def test_kernel_package(self):
        """Test that kernel packages cannot use container validation."""
        assert can_use_container_validation(("linux-image-generic",)) is False

    def test_bootloader_package(self):
        """Test that bootloader packages cannot use container validation."""
        assert can_use_container_validation(("grub-pc",)) is False

    def test_firmware_package(self):
        """Test that firmware packages cannot use container validation."""
        assert can_use_container_validation(("firmware-linux",)) is False

    def test_mixed_packages(self):
        """Test that one no-container package blocks all."""
        assert can_use_container_validation(("nginx", "linux-image-generic")) is False

    def test_list_input(self):
        """Test that list input works."""
        assert can_use_container_validation(["nginx"]) is True


class TestGetRiskSummary:
    """Tests for get_risk_summary function."""

    def test_summary_contains_level(self):
        """Test that summary contains risk level."""
        assessment = classify_risk(("nginx",))
        summary = get_risk_summary(assessment)
        assert "HIGH" in summary

    def test_summary_contains_factors(self):
        """Test that summary contains factors."""
        assessment = classify_risk(("systemd",))
        summary = get_risk_summary(assessment)
        assert "system-critical" in summary.lower()

    def test_summary_contains_tier(self):
        """Test that summary contains recommended tier."""
        assessment = classify_risk(("nginx",))
        summary = get_risk_summary(assessment)
        assert "Tier" in summary or "tier" in summary


class TestPackagePatternSets:
    """Tests for package pattern sets."""

    def test_critical_packages_not_empty(self):
        """Test that critical packages set is populated."""
        assert len(CRITICAL_PACKAGES) > 0

    def test_high_risk_packages_not_empty(self):
        """Test that high-risk packages set is populated."""
        assert len(HIGH_RISK_PACKAGES) > 0

    def test_no_overlap_critical_high(self):
        """Test no overlap between critical and high-risk (exact matches)."""
        # Note: wildcard patterns may appear to overlap
        exact_critical = {p for p in CRITICAL_PACKAGES if "*" not in p}
        exact_high = {p for p in HIGH_RISK_PACKAGES if "*" not in p}
        overlap = exact_critical & exact_high
        assert len(overlap) == 0, f"Overlap found: {overlap}"

    def test_no_container_packages_populated(self):
        """Test that no-container packages set is populated."""
        assert len(NO_CONTAINER_PACKAGES) > 0
        assert any("linux-" in p for p in NO_CONTAINER_PACKAGES)
