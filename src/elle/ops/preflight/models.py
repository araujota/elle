"""Data models for pre-flight safety testing.

Defines Pydantic models for validation results, issues,
and test specifications.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PreflightStatus(str, Enum):
    """Overall result status of pre-flight validation."""

    SAFE = "safe"  # All checks passed, safe to proceed
    WARNING = "warning"  # Minor issues, can proceed with caution
    BLOCKED = "blocked"  # Critical issues, should not proceed
    SKIPPED = "skipped"  # Validation was skipped (e.g., low-risk package)


class IssueSeverity(str, Enum):
    """Severity level of a pre-flight issue."""

    INFO = "info"  # Informational, no action needed
    WARNING = "warning"  # Should review before proceeding
    ERROR = "error"  # May cause problems
    CRITICAL = "critical"  # Will likely break the system


class PreflightIssue(BaseModel):
    """A single issue detected during pre-flight validation.

    Issues are warnings or errors that may affect the operation.
    """

    model_config = ConfigDict(frozen=True)

    severity: IssueSeverity = Field(description="Issue severity level")
    message: str = Field(description="Human-readable issue description")
    recommendation: str | None = Field(
        default=None,
        description="Suggested action to address the issue",
    )
    source: Literal["apt", "nspawn", "lxd", "risk"] = Field(
        default="apt",
        description="Which validation tier detected this issue",
    )
    package: str | None = Field(
        default=None,
        description="Package that caused this issue (if applicable)",
    )
    technical_detail: str | None = Field(
        default=None,
        description="Technical details for debugging",
    )


class PreflightResult(BaseModel):
    """Result of pre-flight validation.

    Contains the overall status and any issues detected.
    """

    model_config = ConfigDict(frozen=True)

    status: PreflightStatus = Field(description="Overall validation result")
    tier_used: int = Field(
        ge=0,
        le=3,
        description="Which validation tier was used (0=skipped, 1=apt, 2=nspawn, 3=lxd)",
    )
    issues: tuple[PreflightIssue, ...] = Field(
        default_factory=tuple,
        description="Issues detected during validation",
    )
    duration_ms: int = Field(
        ge=0,
        description="Validation duration in milliseconds",
    )
    can_proceed: bool = Field(
        description="Whether it's safe to proceed with the operation",
    )

    # Detailed results
    apt_output: str | None = Field(
        default=None,
        description="Raw apt --dry-run output",
    )
    nspawn_output: str | None = Field(
        default=None,
        description="Raw systemd-nspawn output (if tier 2 used)",
    )

    # Package analysis
    packages_to_install: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Packages that would be installed",
    )
    packages_to_upgrade: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Packages that would be upgraded",
    )
    packages_to_remove: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Packages that would be removed",
    )
    download_size_mb: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated download size in MB",
    )
    disk_space_required_mb: float = Field(
        default=0.0,
        description="Estimated disk space change in MB (negative means freed)",
    )

    # Validation timestamp
    validated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When validation was performed",
    )

    @property
    def has_errors(self) -> bool:
        """Check if any error or critical issues were detected."""
        return any(issue.severity in (IssueSeverity.ERROR, IssueSeverity.CRITICAL) for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        """Check if any warning issues were detected."""
        return any(issue.severity == IssueSeverity.WARNING for issue in self.issues)

    @property
    def error_count(self) -> int:
        """Count of error/critical issues."""
        return sum(1 for issue in self.issues if issue.severity in (IssueSeverity.ERROR, IssueSeverity.CRITICAL))

    @property
    def warning_count(self) -> int:
        """Count of warning issues."""
        return sum(1 for issue in self.issues if issue.severity == IssueSeverity.WARNING)


class PreflightTest(BaseModel):
    """Specification for a pre-flight test.

    Describes what packages to test and the operation type.
    """

    model_config = ConfigDict(frozen=True)

    packages: tuple[str, ...] = Field(
        description="Packages to test",
    )
    operation: Literal["install", "upgrade", "remove"] = Field(
        description="Type of package operation",
    )
    high_risk: bool = Field(
        default=False,
        description="Whether this involves high-risk packages",
    )
    force_tier: int | None = Field(
        default=None,
        ge=1,
        le=3,
        description="Force a specific validation tier (for testing)",
    )

    # Optional context
    current_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Current installed versions of packages",
    )
    target_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Target versions to install/upgrade to",
    )

    # Result (filled after validation)
    result: PreflightResult | None = Field(
        default=None,
        description="Validation result (set after running)",
    )


class NspawnConfig(BaseModel):
    """Configuration for systemd-nspawn container validation.

    Controls how the ephemeral container is created and run.
    """

    model_config = ConfigDict(frozen=True)

    base_image_path: str = Field(
        default="/var/lib/elle/preflight/base-image",
        description="Path to the base Ubuntu 24.04 image",
    )
    overlay_path: str = Field(
        default="/var/lib/elle/preflight/overlays",
        description="Path for overlay filesystems",
    )
    timeout_sec: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Maximum time for container operations",
    )
    network_enabled: bool = Field(
        default=True,
        description="Whether to enable network in container",
    )
    apt_cache_path: str | None = Field(
        default="/var/cache/apt/archives",
        description="Path to apt cache (shared with host for speed)",
    )


class PreflightConfig(BaseModel):
    """Overall configuration for pre-flight validation."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=True,
        description="Whether pre-flight validation is enabled",
    )
    auto_tier_selection: bool = Field(
        default=True,
        description="Automatically select validation tier based on risk",
    )
    default_tier: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Default tier when auto-selection is disabled",
    )
    nspawn: NspawnConfig = Field(
        default_factory=NspawnConfig,
        description="systemd-nspawn configuration",
    )
    skip_low_risk: bool = Field(
        default=True,
        description="Skip validation for low-risk packages",
    )
    confirm_on_warning: bool = Field(
        default=True,
        description="Require confirmation when warnings are detected",
    )
    block_on_error: bool = Field(
        default=True,
        description="Block operation when errors are detected",
    )
