"""Pydantic models for the Dependency system.

Defines data structures for dependency specifications, check results,
user preferences, and installation operations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Dependency Specification
# =============================================================================


class DependencySpec(BaseModel):
    """Specification for a system dependency.

    Defines everything needed to detect and install a dependency:
    package names, detection methods, and metadata about usage.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    name: str = Field(description="Unique identifier (e.g., 'augeas', 'docker')")
    display_name: str = Field(description="Human-readable name (e.g., 'Augeas Configuration Editor')")
    description: str = Field(description="Why this dependency is needed")

    # Installation
    apt_packages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="APT packages to install (e.g., ('augeas-tools', 'python3-augeas'))",
    )
    pip_packages: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Pip packages to install if not available via apt",
    )

    # Detection methods (at least one should be set)
    check_import: str | None = Field(
        default=None,
        description="Python import to check (e.g., 'augeas')",
    )
    check_command: str | None = Field(
        default=None,
        description="Shell command to check (e.g., 'augtool')",
    )
    check_file: str | None = Field(
        default=None,
        description="Path to check exists (e.g., '/usr/share/augeas/lenses')",
    )

    # Metadata
    required_by: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Capability names that require this dependency",
    )
    optional: bool = Field(
        default=False,
        description="If true, capability degrades gracefully without this dependency",
    )


# =============================================================================
# Check Result
# =============================================================================


class DependencyCheckResult(BaseModel):
    """Result of checking dependency availability.

    Contains information about whether a dependency is available
    and what command would install it if missing.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    dependency: str = Field(description="DependencySpec.name")

    # Status
    available: bool = Field(description="Whether the dependency is available")
    check_method: str = Field(description="Method used to check: 'import', 'command', 'file', 'none'")

    # Error info (if unavailable)
    error_message: str | None = Field(default=None, description="Why the check failed")

    # Installation info (if unavailable)
    install_command: str | None = Field(
        default=None, description="Command to install (e.g., 'sudo apt install augeas-tools python3-augeas')"
    )


# =============================================================================
# User Preference
# =============================================================================

PreferenceChoice = Literal["always_install", "always_skip", "ask"]


class DependencyPreference(BaseModel):
    """User preference for handling a dependency.

    Remembers what the user wants to do when a dependency is missing.
    """

    model_config = ConfigDict(frozen=True)

    dependency: str = Field(description="DependencySpec.name")
    preference: PreferenceChoice = Field(description="What to do when dependency is missing")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="When preference was first set"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="When preference was last updated"
    )


# =============================================================================
# Installation Request/Result
# =============================================================================


class InstallationRequest(BaseModel):
    """Request to install a dependency.

    Contains all information needed to perform a secure installation.
    """

    model_config = ConfigDict(frozen=True)

    dependency: str = Field(description="DependencySpec.name")
    apt_packages: tuple[str, ...] = Field(description="APT packages to install")
    pip_packages: tuple[str, ...] = Field(default_factory=tuple, description="Pip packages to install")
    reason: str = Field(description="Why this installation is needed (for audit)")


class InstallationResult(BaseModel):
    """Result of an installation attempt.

    Records what happened during installation for audit trail.
    """

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether installation succeeded")
    dependency: str = Field(description="DependencySpec.name")
    installed_packages: tuple[str, ...] = Field(default_factory=tuple, description="Packages that were installed")
    error_message: str | None = Field(default=None, description="Error message if failed")
    duration_sec: float = Field(default=0.0, description="Time taken to install in seconds")
    installed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="When installation was attempted"
    )
