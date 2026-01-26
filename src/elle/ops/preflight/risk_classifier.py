"""Risk classification for package operations.

Determines the risk level of package operations to select
the appropriate validation tier:
- Low risk: Skip validation or use Tier 1 only
- Medium risk: Use Tier 1 (apt --dry-run)
- High risk: Use Tier 2 (systemd-nspawn container)
- Critical risk: Use Tier 3 (LXD full snapshot) or block

Risk factors:
- System critical packages (systemd, libc, kernel)
- Package management tools (apt, dpkg)
- Network infrastructure (netplan, networkmanager)
- Security packages (openssh, openssl, ca-certificates)
- Major version changes
"""

import fnmatch
import logging
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """Risk level for package operations."""

    NONE = "none"  # No risk, skip validation
    LOW = "low"  # Low risk, Tier 1 only
    MEDIUM = "medium"  # Medium risk, Tier 1 required
    HIGH = "high"  # High risk, Tier 2 required
    CRITICAL = "critical"  # Critical risk, Tier 3 or block

    @property
    def order(self) -> int:
        """Get numeric order for comparison."""
        order_map = {
            "none": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        }
        return order_map.get(self.value, 2)


class RiskAssessment(BaseModel):
    """Assessment of package operation risk.

    Contains the overall risk level and factors that
    contributed to the assessment.
    """

    model_config = ConfigDict(frozen=True)

    level: RiskLevel = Field(description="Overall risk level")
    factors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Risk factors that contributed to the assessment",
    )
    recommended_tier: int = Field(
        ge=1,
        le=3,
        description="Recommended validation tier",
    )
    require_confirmation: bool = Field(
        default=False,
        description="Whether to require user confirmation",
    )
    allow_skip: bool = Field(
        default=True,
        description="Whether user can skip validation",
    )


# =============================================================================
# High-risk package patterns
# =============================================================================

# System critical packages - changes can break boot/system
CRITICAL_PACKAGES = frozenset(
    {
        # Init and core
        "systemd",
        "systemd-sysv",
        "init",
        "sysvinit-core",
        # Kernel
        "linux-image-*",
        "linux-headers-*",
        "linux-generic",
        "linux-virtual",
        # Boot
        "grub-*",
        "grub2-common",
        "shim-signed",
        # C library (breaking this bricks the system)
        "libc6",
        "libc-bin",
        "libc6-dev",
        "libgcc-s1",
        # Package management
        "apt",
        "apt-utils",
        "dpkg",
        "aptitude",
        # SSL/TLS (security critical)
        "openssl",
        "libssl*",
        "ca-certificates",
        "gnutls-bin",
    }
)

# High-risk packages - can cause service outages
HIGH_RISK_PACKAGES = frozenset(
    {
        # Network infrastructure
        "netplan.io",
        "networkmanager",
        "network-manager",
        "systemd-resolved",
        "systemd-networkd",
        "ifupdown",
        "iproute2",
        # Security
        "openssh-server",
        "openssh-client",
        "libpam-*",
        "sudo",
        "polkit*",
        "apparmor*",
        # DNS
        "bind9",
        "dnsmasq",
        "unbound",
        # Web servers
        "nginx",
        "apache2",
        # Databases (data loss risk)
        "postgresql*",
        "mysql-server*",
        "mariadb-server*",
        "mongodb-server*",
        "redis-server",
        # Container runtime
        "docker*",
        "containerd*",
        "podman",
        "lxc*",
        "lxd*",
    }
)

# Medium-risk packages - may affect services
MEDIUM_RISK_PACKAGES = frozenset(
    {
        # Python (many tools depend on this)
        "python3",
        "python3-minimal",
        "libpython3*",
        # Interpreters
        "perl",
        "ruby*",
        "nodejs",
        # System utilities
        "coreutils",
        "util-linux",
        "procps",
        "findutils",
        # Logging
        "rsyslog",
        "syslog-ng*",
        "journalctl",
        # Time
        "chrony",
        "ntp",
        "systemd-timesyncd",
        # Mail
        "postfix",
        "exim4*",
        "sendmail*",
    }
)

# Low-risk packages - usually safe to update
LOW_RISK_PACKAGES = frozenset(
    {
        "man-db",
        "manpages*",
        "vim",
        "nano",
        "less",
        "curl",
        "wget",
        "git",
        "htop",
        "tmux",
        "screen",
    }
)

# Packages that should never be validated in container (need real hardware)
NO_CONTAINER_PACKAGES = frozenset(
    {
        "linux-image-*",
        "linux-headers-*",
        "grub-*",
        "shim-signed",
        "fwupd",
        "firmware-*",
        "*-firmware",
    }
)


def _matches_pattern(package: str, patterns: frozenset[str]) -> bool:
    """Check if a package matches any pattern in the set.

    Args:
        package: Package name to check.
        patterns: Set of patterns (may include wildcards).

    Returns:
        True if package matches any pattern.
    """
    return any(fnmatch.fnmatch(package, pattern) for pattern in patterns)


def _get_package_risk_level(package: str) -> tuple[RiskLevel, str]:
    """Get the risk level for a single package.

    Args:
        package: Package name to assess.

    Returns:
        Tuple of (risk_level, factor_description).
    """
    # Normalize package name (remove version specifiers)
    base_package = package.split("=")[0].split("/")[0]

    if _matches_pattern(base_package, CRITICAL_PACKAGES):
        return (RiskLevel.CRITICAL, f"{base_package} is a system-critical package")

    if _matches_pattern(base_package, HIGH_RISK_PACKAGES):
        return (RiskLevel.HIGH, f"{base_package} is a high-risk service package")

    if _matches_pattern(base_package, MEDIUM_RISK_PACKAGES):
        return (RiskLevel.MEDIUM, f"{base_package} is a medium-risk package")

    if _matches_pattern(base_package, LOW_RISK_PACKAGES):
        return (RiskLevel.LOW, f"{base_package} is a low-risk utility package")

    # Default to medium for unknown packages
    return (RiskLevel.MEDIUM, f"{base_package} is an unclassified package")


def classify_risk(
    packages: tuple[str, ...],
    operation: Literal["install", "upgrade", "remove"] = "install",
) -> RiskAssessment:
    """Classify the risk level of a package operation.

    Args:
        packages: Packages to assess.
        operation: Type of operation.

    Returns:
        RiskAssessment with overall risk level and factors.
    """
    if not packages:
        return RiskAssessment(
            level=RiskLevel.NONE,
            factors=("No packages specified",),
            recommended_tier=1,
            require_confirmation=False,
            allow_skip=True,
        )

    factors: list[str] = []
    max_level = RiskLevel.NONE

    # Check each package
    for package in packages:
        level, factor = _get_package_risk_level(package)

        if level.order > max_level.order:
            max_level = level

        if level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            factors.append(factor)

    # Remove operations are inherently riskier
    if operation == "remove":
        if max_level == RiskLevel.LOW:
            max_level = RiskLevel.MEDIUM
            factors.append("Package removal is riskier than installation")
        elif max_level == RiskLevel.MEDIUM:
            max_level = RiskLevel.HIGH
            factors.append("Removing medium-risk packages elevates risk")

    # Multiple packages increase risk slightly
    if len(packages) > 10:
        factors.append(f"Large operation: {len(packages)} packages")
        if max_level == RiskLevel.LOW:
            max_level = RiskLevel.MEDIUM

    # Determine recommended tier
    if max_level == RiskLevel.CRITICAL:
        recommended_tier = 3
    elif max_level == RiskLevel.HIGH:
        recommended_tier = 2
    else:
        recommended_tier = 1

    # Determine if confirmation is required
    require_confirmation = max_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    # Can skip only for low/none risk
    allow_skip = max_level in (RiskLevel.NONE, RiskLevel.LOW)

    return RiskAssessment(
        level=max_level,
        factors=tuple(factors) if factors else ("Standard package operation",),
        recommended_tier=recommended_tier,
        require_confirmation=require_confirmation,
        allow_skip=allow_skip,
    )


def is_high_risk(packages: tuple[str, ...] | list[str]) -> bool:
    """Quick check if any packages are high-risk.

    Args:
        packages: Packages to check.

    Returns:
        True if any package is high or critical risk.
    """
    if isinstance(packages, list):
        packages = tuple(packages)

    assessment = classify_risk(packages)
    return assessment.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def can_use_container_validation(packages: tuple[str, ...] | list[str]) -> bool:
    """Check if packages can be validated in a container.

    Some packages (kernel, bootloader, firmware) need real hardware
    and cannot be tested in a container.

    Args:
        packages: Packages to check.

    Returns:
        True if container validation is possible.
    """
    for package in packages:
        base_package = package.split("=")[0].split("/")[0]
        if _matches_pattern(base_package, NO_CONTAINER_PACKAGES):
            logger.debug(f"Package {package} cannot be validated in container")
            return False
    return True


def get_risk_summary(assessment: RiskAssessment) -> str:
    """Get a human-readable summary of a risk assessment.

    Args:
        assessment: The risk assessment.

    Returns:
        Formatted summary string.
    """
    level_descriptions = {
        RiskLevel.NONE: "No risk detected",
        RiskLevel.LOW: "Low risk - standard validation",
        RiskLevel.MEDIUM: "Medium risk - thorough validation recommended",
        RiskLevel.HIGH: "High risk - container validation recommended",
        RiskLevel.CRITICAL: "Critical risk - full snapshot validation required",
    }

    lines = [
        f"Risk Level: {assessment.level.value.upper()}",
        level_descriptions[assessment.level],
        "",
        "Factors:",
    ]

    for factor in assessment.factors:
        lines.append(f"  - {factor}")

    lines.extend(
        [
            "",
            f"Recommended Tier: {assessment.recommended_tier}",
        ]
    )

    if assessment.require_confirmation:
        lines.append("User confirmation required")

    return "\n".join(lines)
