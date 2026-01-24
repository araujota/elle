"""Package management capabilities.

Provides typed, policy-governed operations for package management:
- package.install - Install a package
- package.remove - Remove a package
- package.update - Update package lists
- package.upgrade - Upgrade packages
- package.info - Get package information
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Input/Output Models
# =============================================================================


class PackageInput(BaseModel):
    """Input for package operations."""

    model_config = ConfigDict(frozen=True)

    package: str = Field(
        description="Package name",
        min_length=1,
    )
    timeout_sec: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="Timeout for the operation in seconds",
    )


class PackageInstallOutput(BaseModel):
    """Output from package install operation."""

    model_config = ConfigDict(frozen=True)

    package: str = Field(description="Package name")
    version: str = Field(default="", description="Installed version")
    already_installed: bool = Field(
        default=False,
        description="Whether package was already installed",
    )


class PackageRemoveOutput(BaseModel):
    """Output from package remove operation."""

    model_config = ConfigDict(frozen=True)

    package: str = Field(description="Package name")
    was_installed: bool = Field(
        default=True,
        description="Whether package was installed before",
    )


class PackageUpdateOutput(BaseModel):
    """Output from package update operation."""

    model_config = ConfigDict(frozen=True)

    packages_updated: int = Field(
        default=0,
        description="Number of package lists updated",
    )


class PackageUpgradeInput(BaseModel):
    """Input for package upgrade operation."""

    model_config = ConfigDict(frozen=True)

    package: str | None = Field(
        default=None,
        description="Specific package to upgrade (None for all)",
    )
    timeout_sec: int = Field(
        default=600,
        ge=60,
        le=7200,
        description="Timeout for the operation in seconds",
    )


class PackageUpgradeOutput(BaseModel):
    """Output from package upgrade operation."""

    model_config = ConfigDict(frozen=True)

    packages_upgraded: int = Field(
        default=0,
        description="Number of packages upgraded",
    )
    packages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="List of upgraded packages",
    )


class PackageInfoOutput(BaseModel):
    """Output from package info operation."""

    model_config = ConfigDict(frozen=True)

    package: str = Field(description="Package name")
    installed: bool = Field(description="Whether package is installed")
    version: str = Field(default="", description="Installed/available version")
    description: str = Field(default="", description="Package description")
    size: str = Field(default="", description="Package size")


# =============================================================================
# Helper Functions
# =============================================================================


def _run_apt(
    command: str,
    timeout: int = 300,
) -> tuple[bool, str, str, int]:
    """Run an apt command.

    Args:
        command: The apt command arguments.
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (success, stdout, stderr, exit_code).
    """
    from elle.cli.subprocess_runner import RunMode, run_safe

    # Add DEBIAN_FRONTEND to avoid interactive prompts
    full_command = f"DEBIAN_FRONTEND=noninteractive apt-get {command}"

    result = run_safe(
        full_command,
        timeout=float(timeout),
        mode=RunMode.CAPTURE,
        check_denylist_flag=False,
    )

    return (result.success, result.stdout, result.stderr, result.exit_code)


def _is_package_installed(package: str) -> tuple[bool, str]:
    """Check if a package is installed.

    Args:
        package: Package name.

    Returns:
        Tuple of (is_installed, version).
    """
    from elle.cli.subprocess_runner import RunMode, run_safe

    result = run_safe(
        f"dpkg-query -W -f='${{Status}} ${{Version}}' {package} 2>/dev/null",
        timeout=10.0,
        mode=RunMode.CAPTURE,
        check_denylist_flag=False,
    )

    if result.success and "install ok installed" in result.stdout:
        # Extract version
        parts = result.stdout.split()
        version = parts[-1] if parts else ""
        return (True, version)

    return (False, "")


# =============================================================================
# Package Install Capability
# =============================================================================


class PackageInstallCapability(BaseCapability):
    """Install a package using apt."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="package.install",
            summary="Install a package using apt",
            domain="package",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="package_change",
                    target="{package}",
                    reversible=True,
                    description="Package will be installed",
                ),
            ),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="PackageInput",
            output_schema_name="PackageInstallOutput",
        )

    def dry_run(self, input: PackageInput) -> DryRunResult:
        """Preview package installation."""
        is_installed, version = _is_package_installed(input.package)

        if is_installed:
            return DryRunResult(
                would_execute=(),
                would_modify=(),
                estimated_risk="none",
                requires_confirmation=False,
                preview_text=f"Package {input.package} is already installed (v{version})",
                is_valid=True,
            )

        # Get what would be installed
        success, stdout, stderr, _ = _run_apt(
            f"install --dry-run -y {input.package}",
            timeout=60,
        )

        if not success:
            return DryRunResult(
                is_valid=False,
                validation_errors=(stderr or "Package not found",),
                preview_text=f"Cannot install {input.package}",
            )

        return DryRunResult(
            would_execute=(f"apt-get install -y {input.package}",),
            would_modify=(f"package:{input.package}",),
            estimated_risk="medium",
            requires_confirmation=True,
            preview_text=f"Would install {input.package}",
            diff=stdout,
            is_valid=True,
        )

    def run(self, input: PackageInput) -> CapabilityResult:
        """Install the package."""
        start_time = time.time()

        # Check if already installed
        is_installed, version = _is_package_installed(input.package)
        if is_installed:
            return CapabilityResult(
                success=True,
                output=PackageInstallOutput(
                    package=input.package,
                    version=version,
                    already_installed=True,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Package {input.package} already installed",
                ),
            )

        # Install
        success, stdout, stderr, exit_code = _run_apt(
            f"install -y {input.package}",
            timeout=input.timeout_sec,
        )

        execution_time = int((time.time() - start_time) * 1000)

        if success:
            _, new_version = _is_package_installed(input.package)

            return CapabilityResult(
                success=True,
                output=PackageInstallOutput(
                    package=input.package,
                    version=new_version,
                    already_installed=False,
                ),
                side_effects_applied=self.spec.side_effects,
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"apt-get install -y {input.package}",),
                    man_vault_citations=("apt-get(8)",),
                    rationale=f"Installed {input.package}",
                ),
            )
        else:
            return CapabilityResult(
                success=False,
                error=stderr or f"apt install failed with exit code {exit_code}",
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"apt-get install -y {input.package}",),
                    rationale=f"Failed to install {input.package}",
                ),
            )

    def verify(self, input: PackageInput) -> VerificationResult:
        """Verify package is installed."""
        is_installed, version = _is_package_installed(input.package)

        return VerificationResult(
            passed=is_installed,
            checks_performed=(f"dpkg-query -W {input.package}",),
            actual_state={"installed": is_installed, "version": version},
            expected_state={"installed": True},
            discrepancies=()
            if is_installed
            else (f"Package {input.package} not installed",),
        )

    def rollback(
        self,
        input: PackageInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by removing the package."""
        if result.output and result.output.already_installed:
            return RollbackResult(
                success=True,
                error="Package was already installed, no rollback needed",
            )

        success, _, stderr, _ = _run_apt(
            f"remove -y {input.package}",
            timeout=input.timeout_sec,
        )

        return RollbackResult(
            success=success,
            error=stderr if not success else None,
            rolled_back=(f"package:{input.package}",) if success else (),
            failed_to_rollback=() if success else (f"package:{input.package}",),
            commands_executed=(f"apt-get remove -y {input.package}",),
        )


# =============================================================================
# Package Remove Capability
# =============================================================================


class PackageRemoveCapability(BaseCapability):
    """Remove a package using apt."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="package.remove",
            summary="Remove a package using apt",
            domain="package",
            risk="high",
            side_effects=(
                SideEffect(
                    kind="package_change",
                    target="{package}",
                    reversible=True,
                    description="Package will be removed",
                ),
            ),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="PackageInput",
            output_schema_name="PackageRemoveOutput",
        )

    def dry_run(self, input: PackageInput) -> DryRunResult:
        """Preview package removal."""
        is_installed, _ = _is_package_installed(input.package)

        if not is_installed:
            return DryRunResult(
                would_execute=(),
                would_modify=(),
                estimated_risk="none",
                requires_confirmation=False,
                preview_text=f"Package {input.package} is not installed",
                is_valid=True,
            )

        # Get what would be removed
        success, stdout, stderr, _ = _run_apt(
            f"remove --dry-run -y {input.package}",
            timeout=60,
        )

        return DryRunResult(
            would_execute=(f"apt-get remove -y {input.package}",),
            would_modify=(f"package:{input.package}",),
            estimated_risk="high",
            requires_confirmation=True,
            preview_text=f"Would remove {input.package}",
            diff=stdout,
            is_valid=True,
        )

    def run(self, input: PackageInput) -> CapabilityResult:
        """Remove the package."""
        start_time = time.time()

        is_installed, _ = _is_package_installed(input.package)
        if not is_installed:
            return CapabilityResult(
                success=True,
                output=PackageRemoveOutput(
                    package=input.package,
                    was_installed=False,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Package {input.package} not installed",
                ),
            )

        success, stdout, stderr, exit_code = _run_apt(
            f"remove -y {input.package}",
            timeout=input.timeout_sec,
        )

        execution_time = int((time.time() - start_time) * 1000)

        if success:
            return CapabilityResult(
                success=True,
                output=PackageRemoveOutput(
                    package=input.package,
                    was_installed=True,
                ),
                side_effects_applied=self.spec.side_effects,
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"apt-get remove -y {input.package}",),
                    man_vault_citations=("apt-get(8)",),
                    rationale=f"Removed {input.package}",
                ),
            )
        else:
            return CapabilityResult(
                success=False,
                error=stderr or f"apt remove failed with exit code {exit_code}",
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=(f"apt-get remove -y {input.package}",),
                    rationale=f"Failed to remove {input.package}",
                ),
            )

    def verify(self, input: PackageInput) -> VerificationResult:
        """Verify package is removed."""
        is_installed, _ = _is_package_installed(input.package)

        return VerificationResult(
            passed=not is_installed,
            checks_performed=(f"dpkg-query -W {input.package}",),
            actual_state={"installed": is_installed},
            expected_state={"installed": False},
            discrepancies=()
            if not is_installed
            else (f"Package {input.package} still installed",),
        )

    def rollback(
        self,
        input: PackageInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by reinstalling the package."""
        if result.output and not result.output.was_installed:
            return RollbackResult(
                success=True,
                error="Package was not installed, no rollback needed",
            )

        success, _, stderr, _ = _run_apt(
            f"install -y {input.package}",
            timeout=input.timeout_sec,
        )

        return RollbackResult(
            success=success,
            error=stderr if not success else None,
            rolled_back=(f"package:{input.package}",) if success else (),
            failed_to_rollback=() if success else (f"package:{input.package}",),
            commands_executed=(f"apt-get install -y {input.package}",),
        )


# =============================================================================
# Package Update Capability
# =============================================================================


class PackageUpdateCapability(BaseCapability):
    """Update package lists."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="package.update",
            summary="Update package lists (apt update)",
            domain="package",
            risk="low",
            side_effects=(),  # Just updates metadata
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="PackageInput",
            output_schema_name="PackageUpdateOutput",
        )

    def dry_run(self, input: PackageInput) -> DryRunResult:
        """Preview package list update."""
        return DryRunResult(
            would_execute=("apt-get update",),
            would_modify=(),
            estimated_risk="low",
            requires_confirmation=False,
            preview_text="Would update package lists",
            is_valid=True,
        )

    def run(self, input: PackageInput) -> CapabilityResult:
        """Update package lists."""
        start_time = time.time()

        success, stdout, stderr, exit_code = _run_apt(
            "update",
            timeout=input.timeout_sec,
        )

        execution_time = int((time.time() - start_time) * 1000)

        if success:
            # Count updated sources
            updated = stdout.count("Hit:") + stdout.count("Get:")

            return CapabilityResult(
                success=True,
                output=PackageUpdateOutput(packages_updated=updated),
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=("apt-get update",),
                    man_vault_citations=("apt-get(8)",),
                    rationale="Updated package lists",
                ),
            )
        else:
            return CapabilityResult(
                success=False,
                error=stderr or f"apt update failed with exit code {exit_code}",
                execution_time_ms=execution_time,
                evidence=CapabilityEvidence(
                    commands_executed=("apt-get update",),
                    rationale="Failed to update package lists",
                ),
            )


# =============================================================================
# Package Info Capability
# =============================================================================


class PackageInfoCapability(BaseCapability):
    """Get package information."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="package.info",
            summary="Get information about a package",
            domain="package",
            risk="none",
            side_effects=(),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="PackageInput",
            output_schema_name="PackageInfoOutput",
        )

    def dry_run(self, input: PackageInput) -> DryRunResult:
        """Preview package info query."""
        return DryRunResult(
            would_execute=(f"apt-cache show {input.package}",),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would get info for {input.package}",
            is_valid=True,
        )

    def run(self, input: PackageInput) -> CapabilityResult:
        """Get package information."""
        from elle.cli.subprocess_runner import RunMode, run_safe

        start_time = time.time()

        is_installed, version = _is_package_installed(input.package)

        # Get package info from apt-cache
        result = run_safe(
            f"apt-cache show {input.package}",
            timeout=30.0,
            mode=RunMode.CAPTURE,
            check_denylist_flag=False,
        )

        execution_time = int((time.time() - start_time) * 1000)

        description = ""
        size = ""
        cache_version = ""

        if result.success and result.stdout:
            for line in result.stdout.split("\n"):
                if line.startswith("Description:"):
                    description = line.split(":", 1)[1].strip()
                elif line.startswith("Version:") and not cache_version:
                    cache_version = line.split(":", 1)[1].strip()
                elif line.startswith("Size:"):
                    size = line.split(":", 1)[1].strip()

        return CapabilityResult(
            success=True,
            output=PackageInfoOutput(
                package=input.package,
                installed=is_installed,
                version=version if is_installed else cache_version,
                description=description,
                size=size,
            ),
            execution_time_ms=execution_time,
            evidence=CapabilityEvidence(
                commands_executed=(f"apt-cache show {input.package}",),
                rationale=f"Retrieved info for {input.package}",
            ),
        )


# =============================================================================
# Exports
# =============================================================================

PACKAGE_CAPABILITIES = [
    PackageInstallCapability,
    PackageRemoveCapability,
    PackageUpdateCapability,
    PackageInfoCapability,
]
