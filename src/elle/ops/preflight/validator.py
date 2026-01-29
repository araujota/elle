"""Multi-tier pre-flight validation orchestrator.

Coordinates the validation tiers:
1. apt --dry-run (always)
2. systemd-nspawn container (high-risk packages)
3. LXD snapshot (critical changes - not yet implemented)

Selects the appropriate tier based on risk classification
and runs validation accordingly.
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

from elle.ops.preflight.apt_validator import AptValidator, create_apt_validator
from elle.ops.preflight.models import (
    IssueSeverity,
    PreflightConfig,
    PreflightIssue,
    PreflightResult,
    PreflightStatus,
    PreflightTest,
)
from elle.ops.preflight.nspawn_validator import (
    NspawnValidator,
    create_nspawn_validator,
)
from elle.ops.preflight.risk_classifier import (
    RiskLevel,
    can_use_container_validation,
    classify_risk,
)

logger = logging.getLogger(__name__)


class PreflightValidator:
    """Multi-tier pre-flight validation orchestrator.

    Coordinates validation across tiers based on package risk:
    - Tier 1 (apt --dry-run): Always run for all packages
    - Tier 2 (systemd-nspawn): Run for high-risk packages
    - Tier 3 (LXD snapshot): Run for critical packages (not yet implemented)
    """

    def __init__(
        self,
        config: PreflightConfig | None = None,
    ) -> None:
        """Initialize the validator.

        Args:
            config: Preflight configuration. Uses defaults if not provided.
        """
        self._config = config or PreflightConfig()

        # Lazy-initialize validators
        self._apt_validator: AptValidator | None = None
        self._nspawn_validator: NspawnValidator | None = None

    @property
    def apt_validator(self) -> AptValidator:
        """Get the apt validator (lazy initialization)."""
        if self._apt_validator is None:
            self._apt_validator = create_apt_validator()
        return self._apt_validator

    @property
    def nspawn_validator(self) -> NspawnValidator:
        """Get the nspawn validator (lazy initialization)."""
        if self._nspawn_validator is None:
            self._nspawn_validator = create_nspawn_validator(config=self._config.nspawn)
        return self._nspawn_validator

    def validate(self, test: PreflightTest) -> PreflightResult:
        """Validate a package operation.

        Runs the appropriate validation tier(s) based on package risk.

        Args:
            test: The pre-flight test specification.

        Returns:
            PreflightResult with validation outcome.
        """
        if not self._config.enabled:
            return PreflightResult(
                status=PreflightStatus.SKIPPED,
                tier_used=0,
                issues=(
                    PreflightIssue(
                        severity=IssueSeverity.INFO,
                        message="Pre-flight validation is disabled",
                        source="risk",
                    ),
                ),
                duration_ms=0,
                can_proceed=True,
            )

        # Determine risk level
        risk = classify_risk(test.packages, test.operation)
        logger.debug(f"Risk assessment: {risk.level.value} for {test.packages}")

        # Determine which tier to use
        if test.force_tier is not None:
            tier = test.force_tier
        elif self._config.auto_tier_selection:
            tier = self._select_tier(risk.level, test)
        else:
            tier = self._config.default_tier

        # Update test with high_risk flag
        test = PreflightTest(
            packages=test.packages,
            operation=test.operation,
            high_risk=risk.level in (RiskLevel.HIGH, RiskLevel.CRITICAL),
            force_tier=test.force_tier,
            current_versions=test.current_versions,
            target_versions=test.target_versions,
        )

        # Skip validation for low-risk if configured
        if self._config.skip_low_risk and risk.level in (RiskLevel.NONE, RiskLevel.LOW) and tier == 1:
            return PreflightResult(
                status=PreflightStatus.SKIPPED,
                tier_used=0,
                issues=(
                    PreflightIssue(
                        severity=IssueSeverity.INFO,
                        message="Skipped validation for low-risk packages",
                        source="risk",
                    ),
                ),
                duration_ms=0,
                can_proceed=True,
            )

        # Run validation
        return self._run_tiered_validation(test, tier, risk.level)

    def _select_tier(
        self,
        risk_level: RiskLevel,
        test: PreflightTest,
    ) -> int:
        """Select the appropriate validation tier.

        Args:
            risk_level: Assessed risk level.
            test: The pre-flight test.

        Returns:
            Tier number (1, 2, or 3).
        """
        match risk_level:
            case RiskLevel.NONE | RiskLevel.LOW:
                return 1

            case RiskLevel.MEDIUM:
                return 1

            case RiskLevel.HIGH:
                # Check if container validation is possible
                if can_use_container_validation(test.packages):
                    return 2
                return 1

            case RiskLevel.CRITICAL:
                # Would use tier 3 (LXD) if implemented
                # For now, fall back to tier 2 or 1
                if can_use_container_validation(test.packages):
                    return 2
                return 1

            case _:
                return 1

    def _run_tiered_validation(
        self,
        test: PreflightTest,
        tier: int,
        risk: RiskLevel,
    ) -> PreflightResult:
        """Run validation at the specified tier.

        Args:
            test: The pre-flight test.
            tier: Validation tier to use.
            risk: Risk assessment.

        Returns:
            PreflightResult from validation.
        """
        # Tier 1: Always run apt --dry-run first
        apt_result = self.apt_validator.validate(test)

        # If apt fails, don't continue to higher tiers
        if apt_result.status == PreflightStatus.BLOCKED:
            return apt_result

        # For tier 1 only, return apt result
        if tier == 1:
            # Add risk-based warning if high risk
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                issues = list(apt_result.issues)
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.WARNING,
                        message="High-risk operation - container validation recommended but not performed",
                        recommendation="Use /preflight --tier=2 for more thorough validation",
                        source="risk",
                    )
                )
                return PreflightResult(
                    status=PreflightStatus.WARNING if issues else apt_result.status,
                    tier_used=1,
                    issues=tuple(issues),
                    duration_ms=apt_result.duration_ms,
                    can_proceed=apt_result.can_proceed,
                    apt_output=apt_result.apt_output,
                    packages_to_install=apt_result.packages_to_install,
                    packages_to_upgrade=apt_result.packages_to_upgrade,
                    packages_to_remove=apt_result.packages_to_remove,
                    download_size_mb=apt_result.download_size_mb,
                    disk_space_required_mb=apt_result.disk_space_required_mb,
                )
            return apt_result

        # Tier 2: Run nspawn container validation
        if tier >= 2:
            nspawn_result = self.nspawn_validator.validate(test)

            # Combine results
            all_issues = list(apt_result.issues) + list(nspawn_result.issues)

            # Determine combined status
            has_errors = any(i.severity in (IssueSeverity.ERROR, IssueSeverity.CRITICAL) for i in all_issues)

            if has_errors:
                status = PreflightStatus.BLOCKED
                can_proceed = False
            elif all_issues:
                status = PreflightStatus.WARNING
                can_proceed = True
            else:
                status = PreflightStatus.SAFE
                can_proceed = True

            return PreflightResult(
                status=status,
                tier_used=2,
                issues=tuple(all_issues),
                duration_ms=apt_result.duration_ms + nspawn_result.duration_ms,
                can_proceed=can_proceed,
                apt_output=apt_result.apt_output,
                nspawn_output=nspawn_result.nspawn_output,
                packages_to_install=apt_result.packages_to_install,
                packages_to_upgrade=apt_result.packages_to_upgrade,
                packages_to_remove=apt_result.packages_to_remove,
                download_size_mb=apt_result.download_size_mb,
                disk_space_required_mb=apt_result.disk_space_required_mb,
            )

        # Tier 3 not yet implemented
        if tier >= 3:
            issues = list(apt_result.issues)
            issues.append(
                PreflightIssue(
                    severity=IssueSeverity.WARNING,
                    message="Tier 3 (LXD snapshot) validation not yet implemented",
                    recommendation="Using Tier 2 validation instead",
                    source="risk",
                )
            )
            # Fall back to tier 2
            return self._run_tiered_validation(test, 2, risk)

        return apt_result


# Module-level validator instance
_validator: PreflightValidator | None = None
_validator_lock = threading.Lock()


def get_validator(config: PreflightConfig | None = None) -> PreflightValidator:
    """Get the shared pre-flight validator instance.

    Args:
        config: Optional configuration (only used on first call).

    Returns:
        The PreflightValidator singleton.
    """
    global _validator
    with _validator_lock:
        if _validator is None:
            _validator = PreflightValidator(config=config)
        return _validator


def validate_packages(
    packages: tuple[str, ...] | list[str],
    operation: Literal["install", "upgrade", "remove"] = "install",
    force_tier: int | None = None,
) -> PreflightResult:
    """Convenience function to validate a package operation.

    Args:
        packages: Packages to validate.
        operation: Type of operation.
        force_tier: Optional tier to force.

    Returns:
        PreflightResult from validation.
    """
    if isinstance(packages, list):
        packages = tuple(packages)

    test = PreflightTest(
        packages=packages,
        operation=operation,
        force_tier=force_tier,
    )

    return get_validator().validate(test)


def format_result_for_display(
    result: PreflightResult,
    use_colors: bool = True,
) -> str:
    """Format a pre-flight result for terminal display.

    Args:
        result: The validation result.
        use_colors: Whether to use ANSI colors.

    Returns:
        Formatted string for display.
    """
    if use_colors:
        try:
            from elle.cli.terminal.renderer import Colors
        except ImportError:
            use_colors = False

    lines = []

    # Header
    tier_names = {0: "Skipped", 1: "apt --dry-run", 2: "systemd-nspawn", 3: "LXD"}
    tier_name = tier_names.get(result.tier_used, f"Tier {result.tier_used}")

    status_colors = {
        PreflightStatus.SAFE: "GREEN",
        PreflightStatus.WARNING: "YELLOW",
        PreflightStatus.BLOCKED: "RED",
        PreflightStatus.SKIPPED: "DIM",
    }

    if use_colors:
        color = getattr(Colors, status_colors.get(result.status, "RESET"), "")
        reset = Colors.RESET
        bold = Colors.BOLD
    else:
        color = reset = bold = ""

    lines.append(f"{bold}Pre-flight Validation ({tier_name}){reset}")
    lines.append("━" * 45)
    lines.append(f"Status: {color}{result.status.value.upper()}{reset}")
    lines.append(f"Duration: {result.duration_ms}ms")

    # Package changes
    if result.packages_to_install:
        lines.append(f"\nPackages to install: {len(result.packages_to_install)}")
        for pkg in result.packages_to_install[:5]:
            lines.append(f"  + {pkg}")
        if len(result.packages_to_install) > 5:
            lines.append(f"  ... and {len(result.packages_to_install) - 5} more")

    if result.packages_to_upgrade:
        lines.append(f"\nPackages to upgrade: {len(result.packages_to_upgrade)}")
        for pkg in result.packages_to_upgrade[:5]:
            lines.append(f"  ^ {pkg}")
        if len(result.packages_to_upgrade) > 5:
            lines.append(f"  ... and {len(result.packages_to_upgrade) - 5} more")

    if result.packages_to_remove:
        lines.append(f"\nPackages to remove: {len(result.packages_to_remove)}")
        for pkg in result.packages_to_remove[:5]:
            lines.append(f"  - {pkg}")
        if len(result.packages_to_remove) > 5:
            lines.append(f"  ... and {len(result.packages_to_remove) - 5} more")

    # Disk usage
    if result.download_size_mb > 0:
        lines.append(f"\nDownload size: {result.download_size_mb:.1f} MB")
    if result.disk_space_required_mb != 0:
        if result.disk_space_required_mb > 0:
            lines.append(f"Disk space needed: {result.disk_space_required_mb:.1f} MB")
        else:
            lines.append(f"Disk space freed: {-result.disk_space_required_mb:.1f} MB")

    # Issues
    if result.issues:
        lines.append(f"\nIssues ({len(result.issues)}):")

        for issue in result.issues:
            severity_symbol = {
                IssueSeverity.INFO: "ℹ",
                IssueSeverity.WARNING: "⚠",
                IssueSeverity.ERROR: "✗",
                IssueSeverity.CRITICAL: "⛔",
            }.get(issue.severity, "•")

            if use_colors:
                severity_color = {
                    IssueSeverity.INFO: Colors.DIM,
                    IssueSeverity.WARNING: Colors.YELLOW,
                    IssueSeverity.ERROR: Colors.RED,
                    IssueSeverity.CRITICAL: Colors.BOLD_RED,
                }.get(issue.severity, "")
            else:
                severity_color = ""

            lines.append(f"  {severity_color}{severity_symbol} {issue.message}{reset}")

            if issue.recommendation:
                lines.append(f"    → {issue.recommendation}")

    # Final verdict
    lines.append("")
    if result.can_proceed:
        if use_colors:
            lines.append(f"{Colors.GREEN}✓ Safe to proceed{Colors.RESET}")
        else:
            lines.append("✓ Safe to proceed")
    else:
        if use_colors:
            lines.append(f"{Colors.RED}✗ Should not proceed{Colors.RESET}")
        else:
            lines.append("✗ Should not proceed")

    return "\n".join(lines)
