"""Tier 1: apt --dry-run validation.

Performs basic package validation using apt's simulation mode:
- Checks for dependency conflicts
- Detects held packages
- Estimates disk space requirements
- Parses package changes

This is the first validation tier, always run before installation.
"""

import logging
import re
import subprocess
import time

from elle.ops.preflight.models import (
    IssueSeverity,
    PreflightIssue,
    PreflightResult,
    PreflightStatus,
    PreflightTest,
)

logger = logging.getLogger(__name__)


# Regex patterns for parsing apt output
PATTERN_TO_INSTALL = re.compile(
    r"The following NEW packages will be installed:\s*\n((?:\s+\S+)+)",
    re.MULTILINE,
)
PATTERN_TO_UPGRADE = re.compile(
    r"The following packages will be upgraded:\s*\n((?:\s+\S+)+)",
    re.MULTILINE,
)
PATTERN_TO_REMOVE = re.compile(
    r"The following packages will be REMOVED:\s*\n((?:\s+\S+)+)",
    re.MULTILINE,
)
PATTERN_HELD = re.compile(
    r"The following packages have been kept back:\s*\n((?:\s+\S+)+)",
    re.MULTILINE,
)
PATTERN_BROKEN = re.compile(
    r"The following packages have unmet dependencies:",
    re.MULTILINE,
)
PATTERN_CONFLICT = re.compile(
    r"Conflicts:\s+(\S+)",
    re.MULTILINE,
)
PATTERN_DOWNLOAD_SIZE = re.compile(
    r"Need to get\s+([\d.,]+)\s*([kMG]?B)",
    re.IGNORECASE,
)
PATTERN_DISK_SPACE = re.compile(
    r"After this operation,\s+([\d.,]+)\s*([kMG]?B)\s+of additional disk space",
    re.IGNORECASE,
)
PATTERN_DISK_FREED = re.compile(
    r"After this operation,\s+([\d.,]+)\s*([kMG]?B)\s+disk space will be freed",
    re.IGNORECASE,
)
PATTERN_NOT_FOUND = re.compile(
    r"E: Unable to locate package\s+(\S+)",
)
PATTERN_VERSION_NOT_FOUND = re.compile(
    r"E: Version '([^']+)' for '([^']+)' was not found",
)


def _parse_size_to_mb(value: str, unit: str) -> float:
    """Parse size string to megabytes.

    Args:
        value: Numeric value as string.
        unit: Size unit (B, kB, MB, GB).

    Returns:
        Size in megabytes.
    """
    try:
        num = float(value.replace(",", ""))
        unit = unit.upper()

        if unit == "B":
            return num / (1024 * 1024)
        elif unit == "KB":
            return num / 1024
        elif unit == "MB":
            return num
        elif unit == "GB":
            return num * 1024
        else:
            return num
    except ValueError:
        return 0.0


def _extract_packages(match: re.Match[str] | None) -> tuple[str, ...]:
    """Extract package names from regex match.

    Args:
        match: Regex match object containing package list.

    Returns:
        Tuple of package names.
    """
    if not match:
        return ()

    text = match.group(1)
    packages = text.split()
    return tuple(p.strip() for p in packages if p.strip())


class AptValidator:
    """Tier 1 validator using apt --dry-run.

    Performs simulation of package operations to detect issues
    before actual installation.
    """

    def __init__(self, timeout_sec: int = 60) -> None:
        """Initialize the apt validator.

        Args:
            timeout_sec: Timeout for apt commands.
        """
        self._timeout = timeout_sec

    def validate(self, test: PreflightTest) -> PreflightResult:
        """Validate a package operation using apt --dry-run.

        Args:
            test: The pre-flight test specification.

        Returns:
            PreflightResult with validation outcome.
        """
        start_time = time.time()
        issues: list[PreflightIssue] = []

        # Build apt command
        cmd = self._build_apt_command(test)

        try:
            # Run apt simulation
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )

            output = result.stdout + result.stderr
            exit_code = result.returncode

            # Parse the output
            packages_to_install = _extract_packages(PATTERN_TO_INSTALL.search(output))
            packages_to_upgrade = _extract_packages(PATTERN_TO_UPGRADE.search(output))
            packages_to_remove = _extract_packages(PATTERN_TO_REMOVE.search(output))

            # Check for held packages
            held = _extract_packages(PATTERN_HELD.search(output))
            if held:
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.WARNING,
                        message=f"The following packages are held back: {', '.join(held)}",
                        recommendation="Use 'apt-mark unhold <package>' to release holds if needed",
                        source="apt",
                        technical_detail=f"Held packages: {held}",
                    )
                )

            # Check for broken dependencies
            if PATTERN_BROKEN.search(output):
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message="Package has unmet dependencies",
                        recommendation="Run 'apt --fix-broken install' or check package sources",
                        source="apt",
                        technical_detail=output,
                    )
                )

            # Check for conflicts
            conflict_match = PATTERN_CONFLICT.search(output)
            if conflict_match:
                conflicting = conflict_match.group(1)
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message=f"Package conflicts with: {conflicting}",
                        recommendation="Remove conflicting package first or choose an alternative",
                        source="apt",
                        package=conflicting,
                    )
                )

            # Check for package not found
            not_found = PATTERN_NOT_FOUND.search(output)
            if not_found:
                pkg = not_found.group(1)
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message=f"Package not found: {pkg}",
                        recommendation="Check package name or update package lists with 'apt update'",
                        source="apt",
                        package=pkg,
                    )
                )

            # Check for version not found
            version_not_found = PATTERN_VERSION_NOT_FOUND.search(output)
            if version_not_found:
                version = version_not_found.group(1)
                pkg = version_not_found.group(2)
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message=f"Version '{version}' not found for package '{pkg}'",
                        recommendation="Check available versions with 'apt policy <package>'",
                        source="apt",
                        package=pkg,
                    )
                )

            # Parse sizes
            download_size_mb = 0.0
            download_match = PATTERN_DOWNLOAD_SIZE.search(output)
            if download_match:
                download_size_mb = _parse_size_to_mb(download_match.group(1), download_match.group(2))

            disk_space_mb = 0.0
            disk_match = PATTERN_DISK_SPACE.search(output)
            if disk_match:
                disk_space_mb = _parse_size_to_mb(disk_match.group(1), disk_match.group(2))

            freed_match = PATTERN_DISK_FREED.search(output)
            if freed_match:
                disk_space_mb = -_parse_size_to_mb(freed_match.group(1), freed_match.group(2))

            # Warn on large operations
            if len(packages_to_install) + len(packages_to_upgrade) > 50:
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.WARNING,
                        message=f"Large operation: {len(packages_to_install)} installs, {len(packages_to_upgrade)} upgrades",
                        recommendation="Review the package list carefully before proceeding",
                        source="apt",
                    )
                )

            if disk_space_mb > 1000:  # More than 1GB
                issues.append(
                    PreflightIssue(
                        severity=IssueSeverity.WARNING,
                        message=f"Large disk space requirement: {disk_space_mb:.0f} MB",
                        recommendation="Ensure sufficient disk space is available",
                        source="apt",
                    )
                )

            # Determine status
            if exit_code != 0 and any(i.severity in (IssueSeverity.ERROR, IssueSeverity.CRITICAL) for i in issues):
                status = PreflightStatus.BLOCKED
                can_proceed = False
            elif issues and any(i.severity == IssueSeverity.WARNING for i in issues):
                status = PreflightStatus.WARNING
                can_proceed = True
            else:
                status = PreflightStatus.SAFE
                can_proceed = True

            duration_ms = int((time.time() - start_time) * 1000)

            return PreflightResult(
                status=status,
                tier_used=1,
                issues=tuple(issues),
                duration_ms=duration_ms,
                can_proceed=can_proceed,
                apt_output=output,
                packages_to_install=packages_to_install,
                packages_to_upgrade=packages_to_upgrade,
                packages_to_remove=packages_to_remove,
                download_size_mb=download_size_mb,
                disk_space_required_mb=disk_space_mb,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            return PreflightResult(
                status=PreflightStatus.BLOCKED,
                tier_used=1,
                issues=(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message=f"apt command timed out after {self._timeout} seconds",
                        recommendation="Check network connection or try again later",
                        source="apt",
                    ),
                ),
                duration_ms=duration_ms,
                can_proceed=False,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"apt validation failed: {e}")
            return PreflightResult(
                status=PreflightStatus.BLOCKED,
                tier_used=1,
                issues=(
                    PreflightIssue(
                        severity=IssueSeverity.ERROR,
                        message=f"apt validation error: {e}",
                        source="apt",
                        technical_detail=str(e),
                    ),
                ),
                duration_ms=duration_ms,
                can_proceed=False,
            )

    def _build_apt_command(self, test: PreflightTest) -> list[str]:
        """Build the apt command for simulation.

        Args:
            test: The pre-flight test specification.

        Returns:
            Command as list of strings.
        """
        cmd = ["apt-get", "--simulate", "--quiet"]

        match test.operation:
            case "install":
                cmd.append("install")
            case "upgrade":
                cmd.append("upgrade")
            case "remove":
                cmd.extend(["remove", "--purge"])

        # Add packages
        cmd.extend(test.packages)

        # Non-interactive
        cmd.append("-y")

        return cmd


def create_apt_validator(timeout_sec: int = 60) -> AptValidator:
    """Create an apt validator instance.

    Args:
        timeout_sec: Timeout for apt commands.

    Returns:
        Configured AptValidator instance.
    """
    return AptValidator(timeout_sec=timeout_sec)
